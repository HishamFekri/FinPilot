"""Installment plans (FINANCIAL_RULES.md §4.13, API_SPEC.md §11).

A plan's money is ordinary ledger entries: a purchase expense and a debt start when it begins, then
a debt repayment (principal) plus interest and fee expenses for each confirmed payment. The schedule
is an expectation and never posts. Payments are idempotent; changes need If-Match.
"""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_active_user, require_writable_user
from app.api.v1.accounts import CurrencyCode, MoneyOut, _if_match, _no_store, write_rate_limit
from app.api.v1.idempotent import run_once
from app.db.models import Transaction, User
from app.db.session import get_db
from app.services import installments, money

router = APIRouter(tags=["installments"], dependencies=[Depends(_no_store)])


def _money(db: Session, minor: int, currency: str) -> MoneyOut:
    exponent = money.currency(db, currency, for_new_records=False).exponent
    return MoneyOut(amount=money.to_amount(minor, exponent), currency=currency)


class ItemOut(BaseModel):
    id: uuid.UUID
    sequence: int
    due_date: date
    principal: MoneyOut
    interest: MoneyOut
    fee: MoneyOut
    total: MoneyOut
    # Principal paid towards it, from the ledger (oldest installment first).
    covered: MoneyOut
    state: Literal["paid", "partial", "due", "overdue", "upcoming", "superseded"]


class PaymentOut(BaseModel):
    id: uuid.UUID
    paid_on: date
    principal: MoneyOut | None
    interest: MoneyOut | None
    fee: MoneyOut | None
    # Voided payments stay in the history, marked, and no longer count.
    voided: bool
    principal_transaction_id: uuid.UUID | None
    interest_transaction_id: uuid.UUID | None
    fee_transaction_id: uuid.UUID | None


class PlanOut(BaseModel):
    id: uuid.UUID
    name: str
    lender: str | None
    currency: str
    status: Literal["active", "archived"]
    origin: Literal["purchase", "loan", "existing"]
    item_price: MoneyOut | None
    down_payment: MoneyOut | None
    principal: MoneyOut
    paid: MoneyOut
    remaining: MoneyOut
    installment_count: int
    paid_count: int
    paid_before_count: int
    remaining_count: int
    interest_mode: Literal["none", "fixed"]
    interest_paid: MoneyOut
    fees_paid: MoneyOut
    scheduled_interest_left: MoneyOut
    next_due: ItemOut | None
    overdue_count: int
    completion_date: date | None
    ahead: MoneyOut
    behind: MoneyOut
    settled: bool
    goal_id: uuid.UUID | None
    goal_name: str | None
    purchase_transaction_id: uuid.UUID | None
    notes: str | None
    version: int
    items: list[ItemOut] | None = None
    superseded: list[ItemOut] | None = None
    payments: list[PaymentOut] | None = None


def _item(db: Session, currency: str, s: installments.ItemState | None, raw: Any = None) -> ItemOut:
    item = s.item if s is not None else raw
    covered = s.covered_minor if s is not None else 0
    total = item.principal_minor + item.interest_minor + item.fee_minor
    return ItemOut(
        id=item.id,
        sequence=item.sequence,
        due_date=item.due_date,
        principal=_money(db, item.principal_minor, currency),
        interest=_money(db, item.interest_minor, currency),
        fee=_money(db, item.fee_minor, currency),
        total=_money(db, total, currency),
        covered=_money(db, covered, currency),
        state=s.state if s is not None else "superseded",  # type: ignore[arg-type]
    )


def _payment(db: Session, v: installments.PlanView, p: Any) -> PaymentOut:
    def part(tx_id: uuid.UUID | None, minor_of: str) -> tuple[MoneyOut | None, bool]:
        if tx_id is None:
            return None, False
        t: Transaction | None = v.transactions.get(tx_id)
        if t is None:
            return None, False
        minor = getattr(t, minor_of) or t.amount_minor
        return _money(db, minor, v.plan.currency), t.status != "posted"

    principal, p_void = part(p.principal_transaction_id, "debt_amount_minor")
    interest, i_void = part(p.interest_transaction_id, "amount_minor")
    fee, f_void = part(p.fee_transaction_id, "amount_minor")
    return PaymentOut(
        id=p.id, paid_on=p.paid_on, principal=principal, interest=interest, fee=fee,
        voided=all(x for x, tid in ((p_void, p.principal_transaction_id),
                   (i_void, p.interest_transaction_id), (f_void, p.fee_transaction_id)) if tid),
        principal_transaction_id=p.principal_transaction_id,
        interest_transaction_id=p.interest_transaction_id,
        fee_transaction_id=p.fee_transaction_id,
    )  # fmt: skip


def _plan_out(db: Session, v: installments.PlanView, *, detail: bool = False) -> PlanOut:
    c = v.plan.currency

    def m(minor: int) -> MoneyOut:
        return _money(db, minor, c)

    return PlanOut(
        id=v.plan.id, name=v.plan.name, lender=v.plan.lender, currency=c,
        status=v.plan.status,  # type: ignore[arg-type]
        origin=_origin_of(db, v),  # type: ignore[arg-type]
        item_price=m(v.plan.item_price_minor) if v.plan.item_price_minor else None,
        down_payment=(
            m(v.plan.down_payment_minor) if v.plan.down_payment_minor is not None else None
        ),
        principal=m(v.principal_minor), paid=m(v.paid_minor), remaining=m(v.remaining_minor),
        installment_count=v.plan.installment_count, paid_count=v.paid_count,
        paid_before_count=v.plan.paid_before_count, remaining_count=v.remaining_count,
        interest_mode=v.plan.interest_mode,  # type: ignore[arg-type]
        interest_paid=m(v.interest_paid_minor), fees_paid=m(v.fees_paid_minor),
        scheduled_interest_left=m(v.scheduled_interest_left),
        next_due=_item(db, c, v.next_item) if v.next_item else None,
        overdue_count=len(v.overdue), completion_date=v.completion_date,
        ahead=m(v.ahead_minor), behind=m(v.behind_minor), settled=v.settled,
        goal_id=v.plan.goal_id, goal_name=v.goal.name if v.goal else None,
        purchase_transaction_id=v.plan.purchase_transaction_id, notes=v.plan.notes,
        version=v.plan.version,
        items=[_item(db, c, s) for s in v.items] if detail else None,
        superseded=[_item(db, c, None, r) for r in v.superseded] if detail else None,
        payments=[_payment(db, v, p) for p in reversed(v.payments)] if detail else None,
    )  # fmt: skip


def _origin_of(db: Session, v: installments.PlanView) -> str:
    """purchase, loan (money arrived in an account) or existing (recorded later, no money moved)."""
    if v.plan.purchase_transaction_id is not None:
        return "purchase"
    start = db.scalar(
        select(Transaction.account_id).where(
            Transaction.debt_id == v.plan.debt_id, Transaction.type == "debt_start"
        )
    )
    return "loan" if start is not None else "existing"


def _etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


# --- Reading


@router.get("/installments")
def list_plans(
    include_archived: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[PlanOut]:
    views = installments.list_for(db, user, include_archived=include_archived)
    return [_plan_out(db, v) for v in views]


@router.get("/installments/{plan_id}")
def get_plan(
    plan_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> PlanOut:
    v = installments.view(db, user, installments.owned(db, user, plan_id))
    _etag(response, v.plan.version)
    return _plan_out(db, v, detail=True)


# --- Creating


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Literal["purchase", "loan", "existing"]
    name: StrictStr = Field(min_length=1, max_length=80)
    lender: StrictStr | None = Field(default=None, max_length=80)
    currency: CurrencyCode
    installment_count: int = Field(ge=1, le=600)
    first_due_date: date
    item_price: StrictStr | None = None
    down_payment: StrictStr | None = None
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    start_date: date | None = None
    principal: StrictStr | None = None
    paid_before_count: int = Field(default=0, ge=0, le=599)
    interest_per_installment: StrictStr | None = None
    fee_per_installment: StrictStr | None = None
    notes: StrictStr | None = Field(default=None, max_length=500)
    goal_id: uuid.UUID | None = None

    def draft(self) -> installments.PlanDraft:
        return installments.PlanDraft(**self.model_dump())


class PreviewOut(BaseModel):
    count: int
    principal: MoneyOut
    per_installment: list[MoneyOut]
    due_dates: list[date]
    interest_per_installment: MoneyOut
    fee_per_installment: MoneyOut
    total_to_pay: MoneyOut
    completion_date: date


@router.post("/installments/preview", dependencies=[write_rate_limit])
def preview_plan(
    body: PlanIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> PreviewOut:
    """The schedule a plan would get. Saves nothing."""
    s = installments.preview(db, user, body.draft())
    c = body.currency
    return PreviewOut(
        count=len(s.due_dates),
        principal=_money(db, sum(s.principal), c),
        per_installment=[_money(db, x, c) for x in s.per_installment],
        due_dates=s.due_dates,
        interest_per_installment=_money(db, s.interest, c),
        fee_per_installment=_money(db, s.fee, c),
        total_to_pay=_money(db, sum(s.per_installment), c),
        completion_date=s.due_dates[-1],
    )


@router.post("/installments", status_code=201, dependencies=[write_rate_limit])
def create_plan(
    request: Request,
    body: PlanIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        plan = installments.create(db, user, body.draft())
        out = _plan_out(db, installments.view(db, user, plan), detail=True)
        return 201, out.model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


# --- Paying


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    principal: StrictStr | None = None
    principal_account_amount: StrictStr | None = None
    interest: StrictStr | None = None
    fee: StrictStr | None = None
    paid_on: date | None = None
    note: StrictStr | None = Field(default=None, max_length=100)


@router.post("/installments/{plan_id}/payments", status_code=201, dependencies=[write_rate_limit])
def record_payment(
    plan_id: uuid.UUID,
    request: Request,
    body: PaymentIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    """A real payment, confirmed by the person. Only this marks installments paid."""

    def operation() -> tuple[int, Any]:
        installments.pay(db, user, plan_id, installments.PaymentDraft(**body.model_dump()))
        plan = installments.owned(db, user, plan_id)
        return 201, _plan_out(db, installments.view(db, user, plan), detail=True).model_dump(
            mode="json"
        )

    payload = {"plan": str(plan_id), **body.model_dump(mode="json")}
    return run_once(db, user, request, idempotency_key, payload, operation)


# --- Changing


class RescheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remaining_count: int = Field(ge=1, le=600)
    first_due_date: date
    interest_per_installment: StrictStr | None = None
    fee_per_installment: StrictStr | None = None


@router.post("/installments/{plan_id}/reschedule", dependencies=[write_rate_limit])
def reschedule_plan(
    plan_id: uuid.UUID,
    body: RescheduleIn,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> PlanOut:
    plan = installments.reschedule(
        db, user, plan_id, _if_match(if_match), installments.RescheduleDraft(**body.model_dump())
    )
    _etag(response, plan.version)
    return _plan_out(db, installments.view(db, user, plan), detail=True)


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    lender: StrictStr | None = Field(default=None, max_length=80)
    notes: StrictStr | None = Field(default=None, max_length=500)
    goal_id: uuid.UUID | None = None


@router.patch("/installments/{plan_id}", dependencies=[write_rate_limit])
def update_plan(
    plan_id: uuid.UUID,
    body: PlanPatch,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> PlanOut:
    plan = installments.update(
        db, user, plan_id, _if_match(if_match), body.model_dump(exclude_unset=True)
    )
    _etag(response, plan.version)
    return _plan_out(db, installments.view(db, user, plan), detail=True)


def _status_route(action: str) -> Any:
    def change(
        plan_id: uuid.UUID,
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
        user: User = Depends(require_writable_user),
        db: Session = Depends(get_db),
    ) -> PlanOut:
        version = _if_match(if_match)
        if action == "remove":
            plan = installments.remove(db, user, plan_id, version)
        else:
            plan = installments.archive(db, user, plan_id, version, action == "archive")
        _etag(response, plan.version)
        return _plan_out(db, installments.view(db, user, plan), detail=True)

    change.__name__ = f"{action}_plan"
    return change


for _action in ("archive", "restore", "remove"):
    router.add_api_route(
        f"/installments/{{plan_id}}/{_action}",
        _status_route(_action),
        methods=["POST"],
        dependencies=[write_rate_limit],
        name=f"installment_{_action}",
    )


# --- Analytics


class WindowOut(BaseModel):
    months: int
    amount: MoneyOut


class CurrencyOverviewOut(BaseModel):
    currency: str
    monthly: MoneyOut
    remaining_principal: MoneyOut
    scheduled_interest: MoneyOut
    remaining_obligations: MoneyOut
    remaining_count: int
    overdue_count: int
    overdue: MoneyOut
    due_within: list[WindowOut]
    # Average monthly income recorded in this currency (the last three full months that had any),
    # and the share of it the monthly installments take. Null when there's no income to compare.
    average_income: MoneyOut | None
    income_months: int
    income_ratio_percent: str | None
    last_payoff: date | None


class UpcomingOut(BaseModel):
    plan_id: uuid.UUID
    debt_id: uuid.UUID
    plan_name: str
    sequence: int
    due_date: date
    amount: MoneyOut
    state: str


class OverviewOut(BaseModel):
    currencies: list[CurrencyOverviewOut]
    upcoming: list[UpcomingOut]
    plan_count: int


@router.get("/installments-overview")
def installments_overview(
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> OverviewOut:
    """Expected obligations, per currency (never converted). Estimates, not spending."""
    views = installments.list_for(db, user)
    currencies, upcoming = installments.overview(db, user, views)

    def ratio(c: installments.CurrencyOverview) -> str | None:
        if not c.average_income_minor:
            return None
        value = (c.monthly_minor * 1000 + c.average_income_minor // 2) // c.average_income_minor
        return f"{value // 10}.{value % 10}"

    return OverviewOut(
        currencies=[
            CurrencyOverviewOut(
                currency=c.currency,
                monthly=_money(db, c.monthly_minor, c.currency),
                remaining_principal=_money(db, c.remaining_principal_minor, c.currency),
                scheduled_interest=_money(db, c.scheduled_interest_minor, c.currency),
                remaining_obligations=_money(
                    db, c.remaining_principal_minor + c.scheduled_interest_minor, c.currency
                ),
                remaining_count=c.remaining_count,
                overdue_count=c.overdue_count,
                overdue=_money(db, c.overdue_minor, c.currency),
                due_within=[
                    WindowOut(months=w, amount=_money(db, amount, c.currency))
                    for w, amount in c.due_within.items()
                ],
                average_income=(
                    _money(db, c.average_income_minor, c.currency)
                    if c.average_income_minor
                    else None
                ),
                income_months=c.income_months,
                income_ratio_percent=ratio(c),
                last_payoff=c.last_payoff,
            )
            for c in currencies
        ],
        upcoming=[
            UpcomingOut(
                plan_id=u.plan.id,
                debt_id=u.plan.debt_id,
                plan_name=u.plan.name,
                sequence=u.state.item.sequence,
                due_date=u.state.item.due_date,
                amount=_money(db, u.amount_minor, u.plan.currency),
                state=u.state.state,
            )
            for u in upcoming
        ],
        plan_count=len(views),
    )
