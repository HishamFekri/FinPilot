"""Setup, categories, income and expense, history, and the monthly summary (A4).

Every route needs an active session; every change also needs CSRF; financial creates also need an
Idempotency-Key. Records are only ever looked up together with the session's user (404 otherwise).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr, StringConstraints, field_validator
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
from app.api.v1.idempotent import run_once
from app.core.errors import ApiError
from app.core.limits import MONTH_PATTERN
from app.db.models import (
    Category,
    Debt,
    FinancialAccount,
    FxRate,
    PersonalLink,
    Transaction,
    Trip,
    User,
)
from app.db.session import get_db
from app.services import (
    categories,
    category_groups,
    conversion,
    fx,
    money,
    setup,
    summary,
    transactions,
)
from app.services.accounts import clean_text, today_for

router = APIRouter(tags=["finance"], dependencies=[Depends(_no_store)])

Kind = Literal["income", "expense"]


def _text(value: str | None, max_length: int) -> str | None:
    if value is None or not value.strip():
        return None
    return clean_text(value, max_length=max_length)


# --- Setup


class SetupOut(BaseModel):
    completed: bool
    needs_currency: bool
    preferred_currency: str | None
    default_account_id: uuid.UUID | None


def _setup_out(db: Session, user: User) -> SetupOut:
    state = setup.state(db, user)
    default = setup.default_account(db, user) if state.completed else None
    return SetupOut(
        completed=state.completed,
        needs_currency=state.needs_currency,
        preferred_currency=user.preferred_currency,
        default_account_id=default.id if default else None,
    )


@router.get("/setup")
def get_setup(user: User = Depends(require_active_user), db: Session = Depends(get_db)) -> SetupOut:
    return _setup_out(db, user)


class SetupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: CurrencyCode | None = None


@router.post("/setup", dependencies=[write_rate_limit])
def run_setup(
    body: SetupIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> SetupOut:
    """Creates the default accounts and categories, once. Repeating it changes nothing."""
    setup.run(db, user, body.currency)
    return _setup_out(db, user)


# --- Categories


class CategoryOut(BaseModel):
    id: uuid.UUID
    kind: str
    # Seeded defaults: the apps translate `system_key`, unless the person gave a `name`.
    system_key: str | None
    name: str | None
    icon: str
    color: str
    status: str
    version: int
    last_used_at: datetime | None = None
    group_id: uuid.UUID | None = None  # expense categories: their group, if any
    is_favourite: bool = False  # pinned first in Quick Add


def _category(c: Category, last_used: datetime | None = None) -> CategoryOut:
    return CategoryOut(
        id=c.id,
        kind=c.kind,
        system_key=c.system_key,
        name=c.name,
        icon=c.icon,
        color=c.color,
        status=c.status,
        version=c.version,
        last_used_at=last_used,
        group_id=c.group_id,
        is_favourite=c.is_favourite,
    )


@router.get("/categories")
def list_categories(
    include_archived: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[CategoryOut]:
    category_groups.ensure(db, user)  # the default groups, once
    if user.setup_completed_at is not None:
        categories.seed_defaults(db, user.id)  # defaults added later (Interest & fees, 0016)
    return [
        _category(v.category, v.last_used_at)
        for v in categories.list_for(db, user, include_archived=include_archived)
    ]


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    name: StrictStr
    icon: Annotated[str, StringConstraints(max_length=40)]
    color: Annotated[str, StringConstraints(max_length=16)]

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return clean_text(value, max_length=40)


@router.post("/categories", status_code=201, dependencies=[write_rate_limit])
def create_category(
    body: CategoryCreate,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> CategoryOut:
    return _category(
        categories.create(
            db, user, kind=body.kind, name=body.name, icon=body.icon, color=body.color
        )
    )


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr | None = None
    icon: Annotated[str, StringConstraints(max_length=40)] | None = None
    color: Annotated[str, StringConstraints(max_length=16)] | None = None
    status: Literal["active", "archived"] | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        return None if value is None else clean_text(value, max_length=40)


@router.patch("/categories/{category_id}", dependencies=[write_rate_limit])
def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdate,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> CategoryOut:
    """Rename (a seeded one back to its default with `name: null`), restyle, archive, restore."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "no_changes", "Nothing to update")
    for field in ("icon", "color", "status"):
        if field in changes and changes[field] is None:
            raise ApiError(422, "validation_error", f"{field} can't be cleared")
    return _category(categories.change(db, user, category_id, _if_match(if_match), changes))


@router.delete("/categories/{category_id}", status_code=204, dependencies=[write_rate_limit])
def delete_category(
    category_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> None:
    """A custom category nothing ever used. Otherwise archive it (409 explains why)."""
    categories.delete(db, user, category_id)


class FavouriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    favourite: bool


@router.put("/categories/{category_id}/favourite", dependencies=[write_rate_limit])
def set_favourite(
    category_id: uuid.UUID,
    body: FavouriteIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> CategoryOut:
    """Pins a category first in Quick Add (or unpins it). Setting it twice changes nothing."""
    category = categories.owned(db, user, category_id, lock=True)
    category.is_favourite = body.favourite
    db.flush()
    return _category(category)


# --- Transactions


class AccountRef(BaseModel):
    id: uuid.UUID
    name: str | None
    system_key: str | None
    currency: str
    status: str


class CategoryRef(BaseModel):
    id: uuid.UUID
    kind: str
    system_key: str | None
    name: str | None
    icon: str
    color: str
    status: str


class FxOut(BaseModel):
    """How an amount entered in another currency became the posted amount (§4.6)."""

    method: Literal["reference", "manual_rate", "actual_amount"]
    # 1 original currency = rate account currency. Implied, for display, when typed as an amount.
    rate: str
    rate_date: date | None
    source: str | None
    provider: str | None
    upstream: str | None


class DebtLink(BaseModel):
    id: uuid.UUID
    counterparty: str
    direction: str
    currency: str


class TripLinkRef(BaseModel):
    """The owner's own link behind a `trip_*` transaction (never shown to anyone else)."""

    link_id: uuid.UUID
    trip_id: uuid.UUID
    trip_name: str
    kind: str
    status: str


class TransactionOut(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    effective_date: date
    amount: MoneyOut
    # Adjustments and opening balances only: whether the balance went up or down.
    direction: str | None
    # Null only for a debt that existed before FinPilot and moved no money.
    account: AccountRef | None
    counter_account: AccountRef | None
    counter_amount: MoneyOut | None
    category: CategoryRef | None
    # Entered in another currency: what was entered, and how it converted. Never added to totals.
    original_amount: MoneyOut | None = None
    fx: FxOut | None = None
    # Debts: which debt, and the amount in its currency (differs only across currencies).
    debt: DebtLink | None = None
    debt_amount: MoneyOut | None = None
    description: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    version: int
    # A `trip_*` transaction: the trip it came from. `replaces_transaction_id`: the hand-entered
    # entry it replaced ("attach existing"), or the trip entry a converted one replaced.
    trip_link: TripLinkRef | None = None
    replaces_transaction_id: uuid.UUID | None = None
    # A refund: the purchase it refunds.
    refunds_transaction_id: uuid.UUID | None = None


class Change(BaseModel):
    at: datetime
    action: Literal["created", "corrected", "voided"]


class RefundSummary(BaseModel):
    """A purchase's refunds: what came back, what's left, and every refund (voided ones too)."""

    refunded: MoneyOut
    refundable: MoneyOut
    items: list[TransactionOut]


class TransactionDetail(TransactionOut):
    changes: list[Change]
    warnings: list[str] = []
    refunds: RefundSummary | None = None  # expenses only


def _transactions_out(db: Session, rows: list[Transaction]) -> list[TransactionOut]:
    account_ids = {r.account_id for r in rows} | {
        r.counter_account_id for r in rows if r.counter_account_id
    }
    category_ids = {r.category_id for r in rows if r.category_id}
    found_accounts = {
        a.id: a
        for a in db.scalars(select(FinancialAccount).where(FinancialAccount.id.in_(account_ids)))
    }
    found_categories = {
        c.id: c for c in db.scalars(select(Category).where(Category.id.in_(category_ids)))
    }
    exponents = money.exponents(
        db,
        {r.currency for r in rows}
        | {r.counter_currency for r in rows if r.counter_currency}
        | {r.original_currency for r in rows if r.original_currency},
    )
    debt_ids = {r.debt_id for r in rows if r.debt_id}
    found_debts = (
        {d.id: d for d in db.scalars(select(Debt).where(Debt.id.in_(debt_ids)))} if debt_ids else {}
    )
    debt_currency = {i: d.currency for i, d in found_debts.items()}
    exponents.update(money.exponents(db, set(debt_currency.values())))
    rate_ids = {r.fx_rate_id for r in rows if r.fx_rate_id}
    found_rates = (
        {x.id: x for x in db.scalars(select(FxRate).where(FxRate.id.in_(rate_ids)))}
        if rate_ids
        else {}
    )

    def fx_out(r: Transaction) -> FxOut | None:
        if r.fx_method is None or r.fx_rate is None:
            return None
        row = found_rates.get(r.fx_rate_id) if r.fx_rate_id else None
        used = row is not None and r.fx_method != "actual_amount"
        return FxOut(
            method=r.fx_method,  # type: ignore[arg-type]
            rate=format(r.fx_rate.normalize(), "f"),
            rate_date=row.rate_date if row else None,
            source=row.source if used and row else None,
            provider=row.provider if used and row else None,
            upstream=row.upstream if used and row else None,
        )

    trip_ids = [r.id for r in rows if r.type.startswith("trip_")]
    trip_links: dict[uuid.UUID, TripLinkRef] = {}
    if trip_ids:
        for link, trip_name in db.execute(
            select(PersonalLink, Trip.name)
            .join(Trip, Trip.id == PersonalLink.trip_id)
            .where(PersonalLink.transaction_id.in_(trip_ids))
            .order_by(PersonalLink.created_at)
        ):
            trip_links[link.transaction_id] = TripLinkRef(
                link_id=link.id, trip_id=link.trip_id, trip_name=trip_name, kind=link.kind,
                status=link.status,
            )  # fmt: skip

    def account(a: FinancialAccount) -> AccountRef:
        return AccountRef(
            id=a.id, name=a.name, system_key=a.system_key, currency=a.currency, status=a.status
        )

    out = []
    for r in rows:
        c = found_categories.get(r.category_id) if r.category_id else None
        counter = found_accounts.get(r.counter_account_id) if r.counter_account_id else None
        out.append(
            TransactionOut(
                id=r.id,
                type=r.type,
                status=r.status,
                effective_date=r.effective_date,
                amount=MoneyOut(
                    amount=money.to_amount(r.amount_minor, exponents[r.currency]),
                    currency=r.currency,
                ),
                direction=r.direction,
                account=account(found_accounts[r.account_id]) if r.account_id else None,
                counter_account=account(counter) if counter else None,
                counter_amount=(
                    MoneyOut(
                        amount=money.to_amount(
                            r.counter_amount_minor or 0, exponents[r.counter_currency or ""]
                        ),
                        currency=r.counter_currency or "",
                    )
                    if r.counter_currency
                    else None
                ),
                category=(
                    CategoryRef(
                        id=c.id,
                        kind=c.kind,
                        system_key=c.system_key,
                        name=c.name,
                        icon=c.icon,
                        color=c.color,
                        status=c.status,
                    )
                    if c
                    else None
                ),
                original_amount=(
                    MoneyOut(
                        amount=money.to_amount(
                            r.original_amount_minor, exponents[r.original_currency]
                        ),
                        currency=r.original_currency,
                    )
                    if r.original_amount_minor is not None and r.original_currency
                    else None
                ),
                fx=fx_out(r),
                trip_link=trip_links.get(r.id),
                replaces_transaction_id=r.replaces_transaction_id,
                refunds_transaction_id=r.refunds_transaction_id,
                debt=(
                    DebtLink(
                        id=found_debts[r.debt_id].id,
                        counterparty=found_debts[r.debt_id].counterparty,
                        direction=found_debts[r.debt_id].direction,
                        currency=found_debts[r.debt_id].currency,
                    )
                    if r.debt_id
                    else None
                ),
                debt_amount=(
                    MoneyOut(
                        amount=money.to_amount(
                            r.debt_amount_minor, exponents[debt_currency[r.debt_id]]
                        ),
                        currency=debt_currency[r.debt_id],
                    )
                    if r.debt_id and r.debt_amount_minor is not None
                    else None
                ),
                description=r.description,
                notes=r.notes,
                created_at=r.created_at,
                updated_at=r.updated_at,
                version=r.version,
            )
        )
    return out


def _detail(db: Session, row: Transaction, warnings: list[str] | None = None) -> TransactionDetail:
    base = _transactions_out(db, [row])[0]
    refunds = None
    if row.type == "expense":
        exponent = money.exponents(db, {row.currency})[row.currency]
        done = transactions.refunded_minor(db, row)
        refunds = RefundSummary(
            refunded=MoneyOut(amount=money.to_amount(done, exponent), currency=row.currency),
            refundable=MoneyOut(
                amount=money.to_amount(
                    max(row.amount_minor - done, 0) if row.status == "posted" else 0, exponent
                ),
                currency=row.currency,
            ),
            items=_transactions_out(db, transactions.refunds_of(db, row)),
        )
    return TransactionDetail(
        **base.model_dump(),
        changes=[Change(at=c.at, action=c.action) for c in transactions.changes_of(db, row)],  # type: ignore[arg-type]
        warnings=warnings or [],
        refunds=refunds,
    )


class ConversionIn(BaseModel):
    """How an amount in another currency becomes the account's (API_SPEC.md §5)."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["reference", "manual_rate", "actual_amount"]
    rate_id: uuid.UUID | None = None
    rate: Annotated[str, StringConstraints(max_length=30)] | None = None
    account_amount: Annotated[str, StringConstraints(max_length=30)] | None = None
    accept_stale: bool = False

    def to_service(self) -> conversion.Conversion:
        return conversion.Conversion(
            method=self.method,
            rate_id=self.rate_id,
            rate=self.rate,
            account_amount=self.account_amount,
            accept_stale=self.accept_stale,
        )


def gateway(request: Request) -> fx.FxGateway:
    found: fx.FxGateway = request.app.state.fx
    return found


class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Kind
    account_id: uuid.UUID
    amount: MoneyIn
    category_id: uuid.UUID
    effective_date: date | None = None
    description: StrictStr | None = None
    notes: StrictStr | None = None
    conversion: ConversionIn | None = None
    # Saved from a quick template (recorded for the person's history; checks are the same).
    source: Literal["quick_add", "template"] = "quick_add"

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        return _text(value, 200)

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: str | None) -> str | None:
        return _text(value, 2000)


@router.post("/transactions", status_code=201, dependencies=[write_rate_limit])
def create_transaction(
    request: Request,
    body: TransactionCreate,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        recorded = transactions.create(
            db,
            user,
            transactions.Draft(
                type=body.type,
                account_id=body.account_id,
                amount=body.amount.amount,
                currency=body.amount.currency,
                category_id=body.category_id,
                effective_date=body.effective_date,
                description=body.description,
                notes=body.notes,
                conversion=body.conversion.to_service() if body.conversion else None,
            ),
            via=body.source,
            gateway=gateway(request),
        )
        detail = _detail(db, recorded.transaction, recorded.warnings)
        return 201, detail.model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn
    account_id: uuid.UUID | None = None
    effective_date: date | None = None
    description: StrictStr | None = None
    notes: StrictStr | None = None
    conversion: ConversionIn | None = None

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        return _text(value, 200)

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: str | None) -> str | None:
        return _text(value, 2000)


@router.post(
    "/transactions/{transaction_id}/refunds", status_code=201, dependencies=[write_rate_limit]
)
def create_refund(
    transaction_id: uuid.UUID,
    request: Request,
    body: RefundCreate,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    """Money back for a purchase (FINANCIAL_RULES.md §4.11). Returns the purchase, with its
    refunds; the refund itself is the last of them."""

    def operation() -> tuple[int, Any]:
        recorded = transactions.refund(
            db,
            user,
            transaction_id,
            transactions.RefundDraft(
                amount=body.amount.amount,
                currency=body.amount.currency,
                account_id=body.account_id,
                effective_date=body.effective_date,
                description=body.description,
                notes=body.notes,
                conversion=body.conversion.to_service() if body.conversion else None,
            ),
            gateway=gateway(request),
        )
        original = transactions.owned(db, user, transaction_id)
        out = _detail(db, original, recorded.warnings)
        return 201, out.model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


class Page(BaseModel):
    items: list[TransactionOut]
    next_cursor: str | None


@router.get("/transactions")
def list_transactions(
    type: Literal[
        "income",
        "expense",
        "transfer",
        "adjustment",
        "opening_balance",
        "debt_start",
        "debt_payment",
        "trip_expense_payment",
        "trip_settlement_paid",
        "trip_settlement_received",
        "refund",
    ]
    | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    include_voided: bool = False,
    currency: CurrencyCode | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> Page:
    rows, next_cursor = transactions.history(
        db,
        user,
        transactions.Filters(
            type=type,
            account_id=account_id,
            category_id=category_id,
            date_from=date_from,
            date_to=date_to,
            search=q.strip() if q and q.strip() else None,
            include_voided=include_voided,
            currency=currency,
        ),
        limit=limit,
        cursor=cursor,
    )
    return Page(items=_transactions_out(db, rows), next_cursor=next_cursor)


@router.get("/transactions/{transaction_id}")
def get_transaction(
    transaction_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> TransactionDetail:
    detail = _detail(db, transactions.owned(db, user, transaction_id))
    response.headers["ETag"] = f'"{detail.version}"'
    return detail


class TransactionUpdate(BaseModel):
    """A correction. Money fields reverse and replace the entry; text fields edit the record."""

    model_config = ConfigDict(extra="forbid")

    amount: MoneyIn | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    effective_date: date | None = None
    description: StrictStr | None = None
    notes: StrictStr | None = None
    conversion: ConversionIn | None = None

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        return _text(value, 200)

    @field_validator("notes")
    @classmethod
    def check_notes(cls, value: str | None) -> str | None:
        return _text(value, 2000)


@router.patch("/transactions/{transaction_id}", dependencies=[write_rate_limit])
def correct_transaction(
    transaction_id: uuid.UUID,
    body: TransactionUpdate,
    request: Request,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> TransactionDetail:
    raw = body.model_dump(exclude_unset=True)
    if not raw:
        raise ApiError(422, "no_changes", "Nothing to update")
    for field in ("account_id", "category_id", "effective_date", "amount", "conversion"):
        if field in raw and raw[field] is None:
            raise ApiError(422, "validation_error", f"{field} can't be cleared")
    changes: dict[str, object] = {k: v for k, v in raw.items() if k not in ("amount", "conversion")}
    if body.amount is not None:
        changes["amount"] = body.amount.amount
        changes["currency"] = body.amount.currency
    if body.conversion is not None:
        changes["conversion"] = body.conversion.to_service()
    document = transactions.correct(
        db, user, transaction_id, _if_match(if_match), changes, gateway=gateway(request)
    )
    detail = _detail(db, document)
    response.headers["ETag"] = f'"{detail.version}"'
    return detail


@router.post("/transactions/{transaction_id}/void", dependencies=[write_rate_limit])
def void_transaction(
    transaction_id: uuid.UUID,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> TransactionDetail:
    document = transactions.void(db, user, transaction_id, _if_match(if_match))
    detail = _detail(db, document)
    response.headers["ETag"] = f'"{detail.version}"'
    return detail


# --- Monthly summary


class TopCategory(BaseModel):
    category: CategoryRef
    amount: MoneyOut


class CurrencySummary(BaseModel):
    currency: str
    expenses: MoneyOut
    income: MoneyOut
    transactions: int
    top_categories: list[TopCategory]
    # All of them, largest first, for the breakdown on Home. Their sum plus `uncategorized` is
    # `expenses`, unless refunds took a category below zero.
    categories: list[TopCategory] = []
    uncategorized: MoneyOut | None = None
    # Trip Mode (§11.10): "Your share" is inside `expenses`; the cash is beside it, never in it.
    trip_shares: MoneyOut | None = None
    trip_paid: MoneyOut | None = None
    trip_received: MoneyOut | None = None


class SummaryOut(BaseModel):
    month: str
    currencies: list[CurrencySummary]


@router.get("/summary")
def month_summary(
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> SummaryOut:
    """Spending, income, and top categories for a month (default: this one, in the user's time
    zone), per currency. Transfers, adjustments, and opening balances are never included."""
    first = date.fromisoformat(f"{month}-01") if month else today_for(user).replace(day=1)
    rows = summary.month_summary(db, user, first)
    category_ids = {cid for r in rows for cid, _ in r.categories}
    found = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_(category_ids)))}
    exponents = money.exponents(db, {r.currency for r in rows})

    def amount(minor: int, code: str) -> MoneyOut:
        return MoneyOut(amount=money.to_amount(minor, exponents[code]), currency=code)

    def top(cid: uuid.UUID, total: int, code: str) -> TopCategory:
        category = found[cid]
        return TopCategory(
            category=CategoryRef(
                id=category.id,
                kind=category.kind,
                system_key=category.system_key,
                name=category.name,
                icon=category.icon,
                color=category.color,
                status=category.status,
            ),
            amount=amount(total, code),
        )

    return SummaryOut(
        month=first.strftime("%Y-%m"),
        currencies=[
            CurrencySummary(
                currency=r.currency,
                expenses=amount(r.expenses_minor, r.currency),
                income=amount(r.income_minor, r.currency),
                # Null when the month has nothing from trips in this currency.
                trip_shares=amount(r.trip_shares_minor, r.currency) if r.has_trips else None,
                trip_paid=amount(r.trip_paid_minor, r.currency) if r.has_trips else None,
                trip_received=amount(r.trip_received_minor, r.currency) if r.has_trips else None,
                transactions=r.transactions,
                top_categories=[
                    top(cid, total, r.currency) for cid, total in r.top if cid in found
                ],
                categories=[
                    top(cid, total, r.currency) for cid, total in r.categories if cid in found
                ],
                uncategorized=(
                    amount(r.uncategorized_minor, r.currency) if r.uncategorized_minor > 0 else None
                ),
            )
            for r in rows
        ],
    )
