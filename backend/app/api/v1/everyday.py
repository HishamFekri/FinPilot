"""Quick templates, savings goals, category groups, the calendar and subscriptions.

Owner-only: every row is looked up with the session's user (404 otherwise). Changes need CSRF;
creates also need an Idempotency-Key; edits need If-Match. None of these routes posts money:
templates open Quick Add, goals track a number, groups organise categories, the calendar and the
subscriptions view read figures the ledger and the recurring engine already hold.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr
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
from app.api.v1.recurring import OccurrenceOut, RuleOut, _occurrences_out, _rules_out
from app.core.errors import ApiError
from app.core.limits import MONTH_PATTERN
from app.db.models import CategoryGroup, QuickTemplate, SavingsGoalEvent, User
from app.db.session import get_db
from app.services import (
    calendar,
    category_groups,
    goals,
    installments,
    money,
    recurring,
    reporting,
    templates,
)
from app.services.accounts import today_for

router = APIRouter(tags=["everyday"], dependencies=[Depends(_no_store)])


def _money(db: Session, minor: int, currency: str) -> MoneyOut:
    exponent = money.currency(db, currency, for_new_records=False).exponent
    return MoneyOut(amount=money.to_amount(minor, exponent), currency=currency)


def _etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


# --- Quick templates


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: Literal["income", "expense"]
    amount: MoneyOut | None  # null: asked each time
    currency: str
    account_id: uuid.UUID | None  # null: the default account
    category_id: uuid.UUID
    description: str | None
    is_favourite: bool
    status: Literal["active", "archived"]
    use_count: int
    last_used_at: datetime | None
    version: int


def _template_out(db: Session, t: QuickTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id, name=t.name, kind=t.kind,  # type: ignore[arg-type]
        amount=_money(db, t.amount_minor, t.currency) if t.amount_minor else None,
        currency=t.currency, account_id=t.account_id, category_id=t.category_id,
        description=t.description, is_favourite=t.is_favourite,
        status=t.status,  # type: ignore[arg-type]
        use_count=t.use_count, last_used_at=t.last_used_at, version=t.version,
    )  # fmt: skip


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(max_length=60)] | None = None
    kind: Literal["income", "expense"] | None = None
    amount: MoneyIn | None = None
    currency: CurrencyCode | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    description: Annotated[StrictStr, Field(max_length=200)] | None = None
    is_favourite: bool = False
    # Save an existing income or expense as a template instead.
    from_transaction_id: uuid.UUID | None = None


@router.get("/templates")
def list_templates(
    include_archived: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[TemplateOut]:
    return [
        _template_out(db, t)
        for t in templates.list_for(db, user, include_archived=include_archived)
    ]


@router.post("/templates", status_code=201, dependencies=[write_rate_limit])
def create_template(
    request: Request,
    body: TemplateIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        if body.from_transaction_id is not None:
            row = templates.from_transaction(
                db, user, body.from_transaction_id, body.name, body.is_favourite
            )
        else:
            if not (body.name and body.kind and body.category_id):
                raise ApiError(422, "validation_error", "Give a name, a kind and a category")
            currency = body.amount.currency if body.amount else body.currency
            if currency is None:
                raise ApiError(422, "validation_error", "Choose a currency")
            row = templates.create(
                db, user,
                templates.TemplateDraft(
                    name=body.name, kind=body.kind, category_id=body.category_id,
                    currency=currency, amount=body.amount.amount if body.amount else None,
                    account_id=body.account_id, description=body.description,
                    is_favourite=body.is_favourite,
                ),
            )  # fmt: skip
        return 201, _template_out(db, row).model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


class TemplatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(max_length=60)] | None = None
    amount: MoneyIn | None = None
    clear_amount: bool = False
    account_id: uuid.UUID | None = None
    clear_account: bool = False
    category_id: uuid.UUID | None = None
    description: Annotated[StrictStr, Field(max_length=200)] | None = None
    is_favourite: bool | None = None
    status: Literal["active", "archived"] | None = None


@router.patch("/templates/{template_id}", dependencies=[write_rate_limit])
def update_template(
    template_id: uuid.UUID,
    body: TemplatePatch,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> TemplateOut:
    changes: dict[str, object] = {
        k: v
        for k, v in body.model_dump(exclude_unset=True).items()
        if k not in ("amount", "clear_amount", "clear_account") and v is not None
    }
    if body.amount is not None:
        changes["amount"], changes["currency"] = body.amount.amount, body.amount.currency
    if body.clear_amount:
        changes["amount"] = None
    if body.clear_account:
        changes["account_id"] = None
    row = templates.update(db, user, template_id, _if_match(if_match), changes)
    _etag(response, row.version)
    return _template_out(db, row)


@router.delete("/templates/{template_id}", status_code=204, dependencies=[write_rate_limit])
def delete_template(
    template_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> None:
    templates.delete(db, user, template_id)


@router.post("/templates/{template_id}/used", dependencies=[write_rate_limit])
def template_used(
    template_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> TemplateOut:
    """Called after a transaction from this template was saved. Posts nothing."""
    return _template_out(db, templates.used(db, user, template_id))


# --- Savings goals


class GoalEventOut(BaseModel):
    id: uuid.UUID
    kind: str
    amount: MoneyOut | None
    target: MoneyOut | None
    status: str | None
    note: str | None
    created_at: datetime


class GoalOut(BaseModel):
    id: uuid.UUID
    name: str
    target: MoneyOut
    target_date: date | None
    status: Literal["active", "paused", "archived"]
    # Tracked by hand: not money in an account, and never added to any balance.
    progress: MoneyOut
    remaining: MoneyOut
    percent: str
    reached: bool
    version: int
    history: list[GoalEventOut] | None = None
    # Set when the goal follows an installment plan: progress is then principal repaid ahead of
    # the schedule, and can't be changed by hand.
    plan_id: uuid.UUID | None = None
    plan_name: str | None = None


def _goal_out(db: Session, v: goals.GoalView, *, history: bool = False) -> GoalOut:
    code = v.goal.currency

    def event(e: SavingsGoalEvent) -> GoalEventOut:
        return GoalEventOut(
            id=e.id, kind=e.kind,
            amount=_money(db, e.amount_minor, code) if e.amount_minor else None,
            target=_money(db, e.target_minor, code) if e.target_minor else None,
            status=e.status, note=e.note, created_at=e.created_at,
        )  # fmt: skip

    return GoalOut(
        id=v.goal.id, name=v.goal.name, target=_money(db, v.goal.target_minor, code),
        target_date=v.goal.target_date, status=v.goal.status,  # type: ignore[arg-type]
        progress=_money(db, v.progress, code), remaining=_money(db, v.remaining, code),
        percent=str(v.percent), reached=v.reached, version=v.goal.version,
        history=[event(e) for e in v.events] if history else None,
        plan_id=v.plan_id, plan_name=v.plan_name,
    )  # fmt: skip


@router.get("/goals")
def list_goals(
    include_archived: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[GoalOut]:
    return [_goal_out(db, v) for v in goals.list_for(db, user, include_archived=include_archived)]


class GoalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(max_length=60)]
    target: MoneyIn
    target_date: date | None = None
    starting: Annotated[StrictStr, Field(max_length=30)] | None = None


@router.post("/goals", status_code=201, dependencies=[write_rate_limit])
def create_goal(
    request: Request,
    body: GoalIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        goal = goals.create(
            db, user, name=body.name, target=body.target.amount, currency=body.target.currency,
            target_date=body.target_date, starting=body.starting,
        )  # fmt: skip
        return 201, _goal_out(db, goals.view(db, goal, with_history=True), history=True).model_dump(
            mode="json"
        )

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.get("/goals/{goal_id}")
def get_goal(
    goal_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> GoalOut:
    goal = goals.owned(db, user, goal_id)
    return _goal_out(db, goals.view(db, goal, with_history=True), history=True)


class GoalMoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[StrictStr, Field(max_length=30)]
    note: Annotated[StrictStr, Field(max_length=200)] | None = None


@router.post("/goals/{goal_id}/{direction}", dependencies=[write_rate_limit])
def move_goal(
    goal_id: uuid.UUID,
    direction: Literal["add", "reduce"],
    request: Request,
    body: GoalMoveIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    """Adds to or takes off the tracked progress. Moves no money between accounts."""

    def operation() -> tuple[int, Any]:
        goal = goals.move(db, user, goal_id, direction, body.amount, body.note)
        return 200, _goal_out(db, goals.view(db, goal, with_history=True), history=True).model_dump(
            mode="json"
        )

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


class GoalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(max_length=60)] | None = None
    target: Annotated[StrictStr, Field(max_length=30)] | None = None
    target_date: date | None = None
    clear_target_date: bool = False
    status: Literal["active", "paused", "archived"] | None = None


@router.patch("/goals/{goal_id}", dependencies=[write_rate_limit])
def update_goal(
    goal_id: uuid.UUID,
    body: GoalPatch,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> GoalOut:
    goal = goals.update(
        db, user, goal_id, _if_match(if_match), name=body.name, target=body.target,
        target_date=body.target_date, clear_target_date=body.clear_target_date,
        status=body.status,
    )  # fmt: skip
    _etag(response, goal.version)
    return _goal_out(db, goals.view(db, goal, with_history=True), history=True)


# --- Category groups


class GroupOut(BaseModel):
    id: uuid.UUID
    system_key: str | None  # defaults: translated unless renamed
    name: str | None
    status: Literal["active", "archived"]
    version: int


def _group_out(g: CategoryGroup) -> GroupOut:
    return GroupOut(id=g.id, system_key=g.system_key, name=g.name, status=g.status,  # type: ignore[arg-type]
                    version=g.version)  # fmt: skip


@router.get("/category-groups")
def list_groups(
    include_archived: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[GroupOut]:
    return [
        _group_out(g) for g in category_groups.list_for(db, user, include_archived=include_archived)
    ]


class GroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(max_length=40)]


@router.post("/category-groups", status_code=201, dependencies=[write_rate_limit])
def create_group(
    body: GroupIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> GroupOut:
    return _group_out(category_groups.create(db, user, body.name))


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(max_length=40)] | None = None
    status: Literal["active", "archived"] | None = None


@router.patch("/category-groups/{group_id}", dependencies=[write_rate_limit])
def update_group(
    group_id: uuid.UUID,
    body: GroupPatch,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> GroupOut:
    row = category_groups.change(
        db, user, group_id, _if_match(if_match), name=body.name, status=body.status
    )
    _etag(response, row.version)
    return _group_out(row)


class MoveCategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: uuid.UUID | None


@router.put("/categories/{category_id}/group", dependencies=[write_rate_limit])
def move_category(
    category_id: uuid.UUID,
    body: MoveCategoryIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    category = category_groups.move(db, user, category_id, body.group_id)
    return {
        "id": str(category.id),
        "group_id": str(category.group_id) if category.group_id else None,
    }


class GroupTotalOut(BaseModel):
    group_id: uuid.UUID | None  # null: categories in no group
    amount: MoneyOut
    percent: str


@router.get("/category-groups/totals")
def group_totals(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    currency: CurrencyCode | None = None,
    trip_allocation: Literal["include", "exclude", "only"] = "include",
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[GroupTotalOut]:
    """Spending per group for a period, in one currency: the same category totals as Insights,
    regrouped. Each category is in one group at most, so nothing is counted twice."""
    today = today_for(user)
    end = date_to or today
    start = date_from or end.replace(day=1)
    if start > end:
        raise ApiError(422, "validation_error", "The start is after the end")
    scope = reporting.Scope(
        start, end, currencies=(currency,) if currency else (), trip_allocation=trip_allocation
    )
    rows = category_groups.totals(db, user, reporting.by_category(db, user, scope))
    if currency is None and rows:
        currency = rows[0].currency
    rows = [r for r in rows if r.currency == currency]
    total = sum(r.amount for r in rows)
    return [
        GroupTotalOut(
            group_id=r.group_id,
            amount=_money(db, r.amount, r.currency),
            percent=str(reporting.percent(r.amount, total)),
        )
        for r in rows
    ]


# --- Calendar


class CalendarDay(BaseModel):
    day: date
    income: MoneyOut
    expenses: MoneyOut  # spending, as everywhere: refunds reduce it, trip shares are in it
    transactions: int
    trip_shares: MoneyOut | None  # the part of `expenses` that is allocated trip shares


class CalendarInstallment(BaseModel):
    """An installment expected that month (§4.13): an expectation, never spending by itself."""

    plan_id: uuid.UUID
    plan_name: str
    sequence: int
    due_date: date
    amount: MoneyOut  # what's still expected for it
    state: str


class CalendarOut(BaseModel):
    month: str
    today: date
    currency: str | None
    currencies: list[str]
    days: list[CalendarDay]
    occurrences: list[OccurrenceOut]
    installments: list[CalendarInstallment] = []


@router.get("/calendar")
def get_calendar(
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    currency: CurrencyCode | None = None,
    account_id: uuid.UUID | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> CalendarOut:
    first = date.fromisoformat(f"{month}-01") if month else today_for(user).replace(day=1)
    m = calendar.month(db, user, first, currency=currency, account_id=account_id)
    days = []
    if m.currency is not None:
        code: str = m.currency
        for d in m.days.values():
            shares = sum(s.amount for s in d.trip_shares)
            days.append(
                CalendarDay(
                    day=d.day,
                    income=_money(db, d.income, code),
                    expenses=_money(db, d.expenses, code),
                    transactions=d.transactions,
                    trip_shares=_money(db, shares, code) if shares else None,
                )
            )
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    due = []
    for v in installments.list_for(db, user):
        for state in v.items:
            if first <= state.item.due_date < last and state.state != "paid":
                expected = (
                    state.outstanding_principal + state.item.interest_minor + state.item.fee_minor
                )
                due.append(
                    CalendarInstallment(
                        plan_id=v.plan.id,
                        plan_name=v.plan.name,
                        sequence=state.item.sequence,
                        due_date=state.item.due_date,
                        amount=_money(db, expected, v.plan.currency),
                        state=state.state,
                    )
                )
    return CalendarOut(
        month=first.strftime("%Y-%m"),
        today=m.today,
        currency=m.currency,
        currencies=m.currencies,
        days=days,
        occurrences=_occurrences_out(db, m.occurrences, m.today),
        installments=sorted(due, key=lambda x: (x.due_date, x.plan_name)),
    )


# --- Subscriptions


class SubscriptionCost(BaseModel):
    currency: str
    monthly: MoneyOut
    annual: MoneyOut
    variable: int
    approximate: bool


class SubscriptionsOut(BaseModel):
    items: list[RuleOut]
    costs: list[SubscriptionCost]
    upcoming: list[OccurrenceOut]


@router.get("/subscriptions")
def list_subscriptions(
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> SubscriptionsOut:
    """Recurring rules marked as subscriptions, their cost, and the next payments. Payments are
    confirmed on the occurrence, as for any recurring item; nothing is marked paid by itself."""
    today = recurring.ensure(db, user)
    rules = calendar.subscriptions(db, user)
    ids = {r.id for r in rules}
    upcoming = [
        (o, r)
        for o, r in recurring.occurrences(
            db, user, date_from=today, date_to=today + timedelta(days=days)
        )
        if r.id in ids and o.status == "pending"
    ]
    return SubscriptionsOut(
        items=_rules_out(db, user, rules, today),
        costs=[
            SubscriptionCost(
                currency=c.currency,
                monthly=_money(db, c.monthly, c.currency),
                annual=_money(db, c.annual, c.currency),
                variable=c.variable,
                approximate=c.approximate,
            )
            for c in calendar.costs(rules)
        ],
        upcoming=_occurrences_out(db, upcoming, today),
    )
