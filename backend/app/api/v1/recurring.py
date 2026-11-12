"""Recurring finances and personal debts (API_SPEC.md §4a, §4b).

Reads bring occurrences up to date first (lazy, idempotent generation). Creating a rule or a debt,
confirming an occurrence, and recording a repayment need an Idempotency-Key; every change needs
CSRF; edits need If-Match. Everything is looked up together with the session's user.
"""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_active_user, require_writable_user
from app.api.v1.accounts import (
    CurrencyCode,
    MoneyIn,
    MoneyOut,
    _if_match,
    _no_store,
    write_rate_limit,
)
from app.api.v1.finance import (
    AccountRef,
    CategoryRef,
    ConversionIn,
    TransactionDetail,
    _detail,
    _text,
    _transactions_out,
    gateway,
)
from app.api.v1.idempotent import run_once
from app.core.errors import ApiError
from app.db.models import (
    Category,
    Debt,
    FinancialAccount,
    RecurringOccurrence,
    RecurringRule,
    Transaction,
    User,
)
from app.db.session import get_db
from app.services import debts, money, recurring, schedule
from app.services.accounts import clean_text

router = APIRouter(tags=["recurring"], dependencies=[Depends(_no_store)])


class DebtRef(BaseModel):
    id: uuid.UUID
    counterparty: str
    direction: str
    currency: str


class OccurrenceOut(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: str
    kind: str
    sequence: int
    total: int | None
    window_start: date
    window_end: date
    # What was expected (null for a variable amount) and, once confirmed, what really happened.
    expected: MoneyOut | None
    actual: MoneyOut | None
    account: AccountRef
    category: CategoryRef | None
    debt: DebtRef | None
    status: Literal["pending", "confirmed", "skipped"]
    # For pending ones, from today: expected (not open yet), pending (open), overdue (passed).
    state: Literal["expected", "pending", "overdue", "confirmed", "skipped"]
    # The window is near enough for a reminder (and not rested with "Not yet" today).
    remind: bool
    transaction_id: uuid.UUID | None
    version: int


class RuleOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    notes: str | None
    amount: MoneyOut | None
    currency: str
    account: AccountRef
    category: CategoryRef | None
    debt: DebtRef | None
    frequency: str
    window_start_day: int | None
    window_end_day: int | None
    month: int | None
    start_date: date
    end_date: date | None
    total_occurrences: int | None
    remind_days_before: int
    reminders: bool
    is_subscription: bool = False
    status: str
    version: int
    next: OccurrenceOut | None


def _refs(
    db: Session, account_ids: set[uuid.UUID], category_ids: set[uuid.UUID], debt_ids: set[uuid.UUID]
) -> tuple[dict[uuid.UUID, AccountRef], dict[uuid.UUID, CategoryRef], dict[uuid.UUID, DebtRef]]:
    accounts = {
        a.id: AccountRef(
            id=a.id, name=a.name, system_key=a.system_key, currency=a.currency, status=a.status
        )
        for a in db.scalars(select(FinancialAccount).where(FinancialAccount.id.in_(account_ids)))
    }
    categories = {
        c.id: CategoryRef(
            id=c.id,
            kind=c.kind,
            system_key=c.system_key,
            name=c.name,
            icon=c.icon,
            color=c.color,
            status=c.status,
        )
        for c in db.scalars(select(Category).where(Category.id.in_(category_ids)))
    }
    found_debts = {
        d.id: DebtRef(
            id=d.id, counterparty=d.counterparty, direction=d.direction, currency=d.currency
        )
        for d in db.scalars(select(Debt).where(Debt.id.in_(debt_ids)))
    }
    return accounts, categories, found_debts


def _money(db: Session, minor: int | None, currency: str) -> MoneyOut | None:
    if minor is None:
        return None
    exponent = money.currency(db, currency, for_new_records=False).exponent
    return MoneyOut(amount=money.to_amount(minor, exponent), currency=currency)


def _occurrences_out(
    db: Session, pairs: list[tuple[RecurringOccurrence, RecurringRule]], today: date
) -> list[OccurrenceOut]:
    if not pairs:
        return []
    accounts, categories, found_debts = _refs(
        db,
        {o.account_id for o, _ in pairs},
        {r.category_id for _, r in pairs if r.category_id},
        {r.debt_id for _, r in pairs if r.debt_id},
    )
    posted = {
        t.id: t
        for t in db.scalars(
            select(Transaction).where(
                Transaction.id.in_({o.transaction_id for o, _ in pairs if o.transaction_id})
            )
        )
    }
    out = []
    for o, r in pairs:
        state = (
            o.status
            if o.status != "pending"
            else schedule.status_of(o.window_start, o.window_end, today)
        )
        rested = o.snoozed_until is not None and o.snoozed_until > today
        actual = posted.get(o.transaction_id) if o.transaction_id else None
        out.append(
            OccurrenceOut(
                id=o.id,
                rule_id=r.id,
                rule_name=r.name,
                kind=r.kind,
                sequence=o.sequence,
                total=r.total_occurrences,
                window_start=o.window_start,
                window_end=o.window_end,
                expected=_money(db, o.expected_amount_minor, o.currency),
                actual=_money(db, actual.amount_minor, actual.currency) if actual else None,
                account=accounts[o.account_id],
                category=categories.get(r.category_id) if r.category_id else None,
                debt=found_debts.get(r.debt_id) if r.debt_id else None,
                status=o.status,  # type: ignore[arg-type]
                state=state,  # type: ignore[arg-type]
                remind=(
                    o.status == "pending"
                    and r.reminders
                    and not rested
                    and (o.window_start - today).days <= r.remind_days_before
                ),
                transaction_id=o.transaction_id,
                version=o.version,
            )
        )
    return out


def _rules_out(db: Session, user: User, rules: list[RecurringRule], today: date) -> list[RuleOut]:
    accounts, categories, found_debts = _refs(
        db,
        {r.account_id for r in rules},
        {r.category_id for r in rules if r.category_id},
        {r.debt_id for r in rules if r.debt_id},
    )
    nexts: dict[uuid.UUID, OccurrenceOut] = {}
    if rules:
        pending = db.execute(
            select(RecurringOccurrence, RecurringRule)
            .join(RecurringRule, RecurringRule.id == RecurringOccurrence.rule_id)
            .where(
                RecurringOccurrence.rule_id.in_([r.id for r in rules]),
                RecurringOccurrence.status == "pending",
            )
            .order_by(RecurringOccurrence.window_start)
        )
        for item in _occurrences_out(db, [(o, r) for o, r in pending], today):
            nexts.setdefault(item.rule_id, item)
    return [
        RuleOut(
            id=r.id,
            kind=r.kind,
            name=r.name,
            notes=r.notes,
            amount=_money(db, r.amount_minor, r.currency),
            currency=r.currency,
            account=accounts[r.account_id],
            category=categories.get(r.category_id) if r.category_id else None,
            debt=found_debts.get(r.debt_id) if r.debt_id else None,
            frequency=r.frequency,
            window_start_day=r.window_start_day,
            window_end_day=r.window_end_day,
            month=r.month,
            start_date=r.start_date,
            end_date=r.end_date,
            total_occurrences=r.total_occurrences,
            remind_days_before=r.remind_days_before,
            reminders=r.reminders,
            is_subscription=r.is_subscription,
            status=r.status,
            version=r.version,
            next=nexts.get(r.id),
        )
        for r in rules
    ]


# --- Rules


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["income", "expense", "debt_payment"]
    name: StrictStr
    notes: StrictStr | None = None
    # Null for a variable amount ("whatever the client pays this month").
    amount: MoneyIn | None = None
    currency: CurrencyCode
    account_id: uuid.UUID
    category_id: uuid.UUID | None = None
    debt_id: uuid.UUID | None = None
    frequency: Literal["weekly", "biweekly", "monthly", "yearly"]
    window_start_day: Annotated[int, Field(ge=1, le=31)] | None = None
    window_end_day: Annotated[int, Field(ge=1, le=31)] | None = None
    month: Annotated[int, Field(ge=1, le=12)] | None = None
    start_date: date | None = None
    end_date: date | None = None
    total_occurrences: Annotated[int, Field(ge=1, le=600)] | None = None
    remind_days_before: Annotated[int, Field(ge=0, le=14)] = 0
    reminders: bool = True
    is_subscription: bool = False

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return clean_text(value, max_length=100)

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: str | None) -> str | None:
        return _text(value, 500)


@router.get("/recurring/rules")
def list_rules(
    user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[RuleOut]:
    today = recurring.ensure(db, user)
    rules = list(
        db.scalars(
            select(RecurringRule)
            .where(RecurringRule.user_id == user.id)
            .order_by(RecurringRule.status, RecurringRule.created_at)
        )
    )
    return _rules_out(db, user, rules, today)


@router.post("/recurring/rules", status_code=201, dependencies=[write_rate_limit])
def create_rule(
    request: Request,
    body: RuleIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        if body.amount is not None and body.amount.currency != body.currency:
            raise ApiError(422, "currency_mismatch", "The amount must be in the rule's currency")
        rule = recurring.create_rule(
            db,
            user,
            recurring.RuleDraft(
                kind=body.kind, name=body.name, notes=body.notes,
                amount=body.amount.amount if body.amount else None, currency=body.currency,
                account_id=body.account_id, category_id=body.category_id, debt_id=body.debt_id,
                frequency=body.frequency, window_start_day=body.window_start_day,
                window_end_day=body.window_end_day, month=body.month, start_date=body.start_date,
                end_date=body.end_date, total_occurrences=body.total_occurrences,
                remind_days_before=body.remind_days_before, reminders=body.reminders,
                is_subscription=body.is_subscription,
            ),
        )  # fmt: skip
        today = recurring.ensure(db, user)
        return 201, _rules_out(db, user, [rule], today)[0].model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


class RuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr | None = None
    notes: StrictStr | None = None
    amount: MoneyIn | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    window_start_day: Annotated[int, Field(ge=1, le=31)] | None = None
    window_end_day: Annotated[int, Field(ge=1, le=31)] | None = None
    month: Annotated[int, Field(ge=1, le=12)] | None = None
    end_date: date | None = None
    total_occurrences: Annotated[int, Field(ge=1, le=600)] | None = None
    remind_days_before: Annotated[int, Field(ge=0, le=14)] | None = None
    reminders: bool | None = None
    is_subscription: bool | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        return None if value is None else clean_text(value, max_length=100)

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: str | None) -> str | None:
        return _text(value, 500)


@router.patch("/recurring/rules/{rule_id}", dependencies=[write_rate_limit])
def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdate,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> RuleOut:
    raw = body.model_dump(exclude_unset=True)
    if not raw:
        raise ApiError(422, "no_changes", "Nothing to update")
    for name in (
        "name", "account_id", "category_id", "remind_days_before", "reminders", "is_subscription",
    ):  # fmt: skip
        if name in raw and raw[name] is None:
            raise ApiError(422, "validation_error", f"{name} can't be cleared")
    changes: dict[str, Any] = {k: v for k, v in raw.items() if k != "amount"}
    if "amount" in raw:
        changes["amount"] = body.amount.amount if body.amount else None
        if body.amount is not None:
            changes["currency"] = body.amount.currency
    rule = recurring.update_rule(db, user, rule_id, _if_match(if_match), changes)
    today = recurring.ensure(db, user)
    out = _rules_out(db, user, [rule], today)[0]
    response.headers["ETag"] = f'"{out.version}"'
    return out


def _status_route(status: str) -> Any:
    def change(
        rule_id: uuid.UUID,
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
        user: User = Depends(require_writable_user),
        db: Session = Depends(get_db),
    ) -> RuleOut:
        rule = recurring.set_status(db, user, rule_id, _if_match(if_match), status)
        today = recurring.ensure(db, user)
        out = _rules_out(db, user, [rule], today)[0]
        response.headers["ETag"] = f'"{out.version}"'
        return out

    return change


for _path, _status in (("pause", "paused"), ("resume", "active"), ("end", "ended")):
    router.add_api_route(
        f"/recurring/rules/{{rule_id}}/{_path}",
        _status_route(_status),
        methods=["POST"],
        dependencies=[write_rate_limit],
        name=f"recurring_rule_{_path}",
    )


# --- Occurrences


@router.delete("/recurring/rules/{rule_id}", status_code=204, dependencies=[write_rate_limit])
def delete_rule(
    rule_id: uuid.UUID,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> None:
    """A recurring item or subscription with no confirmed payment. Otherwise end it (409)."""
    recurring.delete_rule(db, user, rule_id, _if_match(if_match))


@router.get("/recurring/occurrences")
def list_occurrences(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    rule_id: uuid.UUID | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[OccurrenceOut]:
    today = recurring.ensure(db, user)
    pairs = recurring.occurrences(db, user, date_from=date_from, date_to=date_to, rule_id=rule_id)
    return _occurrences_out(db, pairs, today)


@router.get("/recurring/upcoming")
def list_upcoming(
    limit: Annotated[int, Query(ge=1, le=10)] = 4,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[OccurrenceOut]:
    """Home's compact list: overdue and open first, then the next two weeks."""
    today = recurring.ensure(db, user)
    return _occurrences_out(db, recurring.upcoming(db, user, today, limit=limit), today)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn
    account_id: uuid.UUID | None = None
    effective_date: date | None = None
    category_id: uuid.UUID | None = None
    description: StrictStr | None = None
    conversion: ConversionIn | None = None
    # Debts across currencies: the amount in the account's currency.
    account_amount: Annotated[str, Field(max_length=30)] | None = None

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        return _text(value, 200)


class ConfirmOut(BaseModel):
    occurrence: OccurrenceOut
    transaction: TransactionDetail


@router.post("/recurring/occurrences/{occurrence_id}/confirm", dependencies=[write_rate_limit])
def confirm_occurrence(
    occurrence_id: uuid.UUID,
    request: Request,
    body: ConfirmIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        occurrence, document, warnings = recurring.confirm(
            db,
            gateway(request),
            user,
            occurrence_id,
            recurring.Confirmation(
                amount=body.amount.amount, currency=body.amount.currency,
                account_id=body.account_id, effective_date=body.effective_date,
                category_id=body.category_id, description=body.description,
                conversion=body.conversion.to_service() if body.conversion else None,
                account_amount=body.account_amount,
            ),
        )  # fmt: skip
        today = recurring.ensure(db, user)
        rule = recurring.owned_rule(db, user, occurrence.rule_id)
        out = ConfirmOut(
            occurrence=_occurrences_out(db, [(occurrence, rule)], today)[0],
            transaction=_detail(db, document, warnings),
        )
        return 200, out.model_dump(mode="json")

    payload = {"occurrence": str(occurrence_id), **body.model_dump(mode="json")}
    return run_once(db, user, request, idempotency_key, payload, operation)


def _answer_route(action: str) -> Any:
    def answer(
        occurrence_id: uuid.UUID,
        user: User = Depends(require_writable_user),
        db: Session = Depends(get_db),
    ) -> OccurrenceOut:
        occurrence = (recurring.skip if action == "skip" else recurring.not_yet)(
            db, user, occurrence_id
        )
        today = recurring.ensure(db, user)
        rule = recurring.owned_rule(db, user, occurrence.rule_id)
        return _occurrences_out(db, [(occurrence, rule)], today)[0]

    return answer


for _action in ("skip", "not-yet"):
    router.add_api_route(
        f"/recurring/occurrences/{{occurrence_id}}/{_action}",
        _answer_route(_action),
        methods=["POST"],
        dependencies=[write_rate_limit],
        name=f"recurring_occurrence_{_action.replace('-', '_')}",
    )


# --- Debts


class DebtOut(BaseModel):
    id: uuid.UUID
    direction: Literal["i_owe", "owed_to_me"]
    counterparty: str
    currency: str
    original: MoneyOut
    # Derived from the ledger, never stored.
    remaining: MoneyOut
    repaid: MoneyOut
    settled: bool
    start_date: date
    notes: str | None
    status: str
    version: int


def _debt_out(db: Session, debt: Debt) -> DebtOut:
    exponent = money.currency(db, debt.currency, for_new_records=False).exponent
    left = debts.remaining(db, debt)
    started = db.scalar(
        select(Transaction.debt_amount_minor).where(
            Transaction.debt_id == debt.id,
            Transaction.type == "debt_start",
            Transaction.status == "posted",
        )
    )
    principal = started or 0
    return DebtOut(
        id=debt.id,
        direction=debt.direction,  # type: ignore[arg-type]
        counterparty=debt.counterparty,
        currency=debt.currency,
        original=MoneyOut(
            amount=money.to_amount(debt.original_amount_minor, exponent), currency=debt.currency
        ),
        remaining=MoneyOut(amount=money.to_amount(left, exponent), currency=debt.currency),
        repaid=MoneyOut(
            amount=money.to_amount(max(principal - left, 0), exponent), currency=debt.currency
        ),
        settled=left == 0,
        start_date=debt.start_date,
        notes=debt.notes,
        status=debt.status,
        version=debt.version,
    )


class DebtIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["i_owe", "owed_to_me"]
    counterparty: StrictStr
    amount: MoneyIn
    start_date: date | None = None
    notes: StrictStr | None = None
    # The account the money went through when it started; omit for a debt that existed before.
    account_id: uuid.UUID | None = None
    account_amount: Annotated[str, Field(max_length=30)] | None = None

    @field_validator("counterparty")
    @classmethod
    def check_counterparty(cls, value: str) -> str:
        return clean_text(value, max_length=100)

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: str | None) -> str | None:
        return _text(value, 500)


@router.get("/debts")
def list_debts(
    user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[DebtOut]:
    rows = db.scalars(
        select(Debt).where(Debt.user_id == user.id).order_by(Debt.status, Debt.created_at)
    )
    return [_debt_out(db, d) for d in rows]


@router.post("/debts", status_code=201, dependencies=[write_rate_limit])
def create_debt(
    request: Request,
    body: DebtIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        debt, _ = debts.create(
            db,
            user,
            debts.DebtDraft(
                direction=body.direction, counterparty=body.counterparty,
                currency=body.amount.currency, amount=body.amount.amount,
                start_date=body.start_date, notes=body.notes, account_id=body.account_id,
                account_amount=body.account_amount,
            ),
        )  # fmt: skip
        return 201, _debt_out(db, debt).model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


class DebtDetail(DebtOut):
    history: list[Any]


@router.get("/debts/{debt_id}")
def get_debt(
    debt_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> DebtDetail:
    debt = debts.owned(db, user, debt_id)
    rows = list(
        db.scalars(
            select(Transaction)
            .where(Transaction.debt_id == debt.id, Transaction.user_id == user.id)
            .order_by(Transaction.effective_date.desc(), Transaction.created_at.desc())
        )
    )
    detail = DebtDetail(**_debt_out(db, debt).model_dump(), history=_transactions_out(db, rows))
    response.headers["ETag"] = f'"{detail.version}"'
    return detail


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    # In the debt's currency.
    amount: MoneyIn
    # In the account's currency, when it differs.
    account_amount: Annotated[str, Field(max_length=30)] | None = None
    effective_date: date | None = None
    description: StrictStr | None = None

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        return _text(value, 200)


@router.post("/debts/{debt_id}/payments", status_code=201, dependencies=[write_rate_limit])
def record_payment(
    debt_id: uuid.UUID,
    request: Request,
    body: PaymentIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        debt = debts.owned(db, user, debt_id)
        if body.amount.currency != debt.currency:
            raise ApiError(422, "currency_mismatch", "The amount is in the debt's currency")
        document = debts.pay(
            db,
            user,
            debt_id,
            debts.Payment(
                account_id=body.account_id, amount=body.amount.amount,
                account_amount=body.account_amount, effective_date=body.effective_date,
                description=body.description,
            ),
        )  # fmt: skip
        return 201, {
            "debt": _debt_out(db, debt).model_dump(mode="json"),
            "transaction": _detail(db, document).model_dump(mode="json"),
        }

    payload = {"debt": str(debt_id), **body.model_dump(mode="json")}
    return run_once(db, user, request, idempotency_key, payload, operation)


def _archive_route(archived: bool) -> Any:
    def change(
        debt_id: uuid.UUID,
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
        user: User = Depends(require_writable_user),
        db: Session = Depends(get_db),
    ) -> DebtOut:
        out = _debt_out(db, debts.archive(db, user, debt_id, _if_match(if_match), archived))
        response.headers["ETag"] = f'"{out.version}"'
        return out

    return change


for _path, _archived in (("archive", True), ("unarchive", False)):
    router.add_api_route(
        f"/debts/{{debt_id}}/{_path}",
        _archive_route(_archived),
        methods=["POST"],
        dependencies=[write_rate_limit],
        name=f"debt_{_path}",
    )
