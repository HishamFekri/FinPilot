"""Budgets, Insights, Reports, and their CSV and PDF exports (API_SPEC.md §7, §13).

Every figure comes from `services.reporting` (FINANCIAL_RULES.md §16.1). Reads run in one
REPEATABLE READ, READ ONLY transaction so a report's summary and table always agree (§16.5).
Budget changes need CSRF. Exports are rate limited.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import rate_limited, require_active_user, require_writable_user
from app.api.v1.accounts import CurrencyCode, MoneyIn, MoneyOut, _no_store, write_rate_limit
from app.api.v1.finance import CategoryRef, TransactionOut, _transactions_out, gateway
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.limits import MIN_DATE, MONTH_PATTERN, check_date
from app.core.rate_limit import Limit
from app.db import rls
from app.db import session as db_session
from app.db.models import Category, FinancialAccount, Transaction, User
from app.db.session import get_db
from app.services import (
    budgets,
    equivalents,
    exports,
    intelligence,
    money,
    recurring,
    reporting,
    transactions,
)
from app.services.accounts import today_for
from app.services.report_text import MONTHS, text

DASH = " – "  # noqa: RUF001 - an en dash between dates
router = APIRouter(tags=["analytics"], dependencies=[Depends(_no_store)])
export_limit = Depends(rate_limited("reports.export", Limit(requests=10, window_seconds=60)))
heavy_read = Depends(rate_limited("analytics.read", Limit(requests=120, window_seconds=60)))
GrainName = Literal["day", "week", "month"]
TypeName = Literal[
    "income", "expense", "transfer", "adjustment", "opening_balance", "debt_start", "debt_payment",
    "trip_expense_payment", "trip_settlement_paid", "trip_settlement_received", "refund",
]  # fmt: skip
# §11.10: whether spending includes the person's allocated trip shares (budgets always do).
AllocationView = Literal["include", "exclude", "only"]


@contextmanager
def snapshot(user: User) -> Iterator[Session]:
    """One consistent, read-only view of the data for a whole report (§16.5)."""
    session = db_session._session_factory()()
    try:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(select(1))  # starts the transaction at that isolation level
        rls.act_as(session, user.id)  # its own session, so it needs the identity too
        yield session
    finally:
        # Closing ends the read-only transaction without expiring what was loaded, so rows read
        # inside the snapshot can still be formatted after it.
        session.close()


def _money(db: Session, minor: int, currency: str) -> MoneyOut:
    exponent = money.currency(db, currency, for_new_records=False).exponent
    return MoneyOut(amount=money.to_amount(minor, exponent), currency=currency)


def _month(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError:
        raise ApiError(422, "validation_error", "Use a month like 2026-11") from None


def _categories(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, CategoryRef]:
    if not ids:
        return {}
    return {
        c.id: CategoryRef(
            id=c.id,
            kind=c.kind,
            system_key=c.system_key,
            name=c.name,
            icon=c.icon,
            color=c.color,
            status=c.status,
        )
        for c in db.scalars(select(Category).where(Category.id.in_(ids)))
    }


class TripShareLine(BaseModel):
    """One allocated share: what a trip expense cost the person (no account, no cash)."""

    trip_id: uuid.UUID
    trip_name: str
    expense_id: uuid.UUID
    description: str
    expense_date: date
    category_key: str
    amount: MoneyOut


class TripShareTotal(BaseModel):
    trip_id: uuid.UUID
    trip_name: str
    amount: MoneyOut
    count: int


def _share_lines(db: Session, lines: list[reporting.ShareLine]) -> list[TripShareLine]:
    return [
        TripShareLine(
            trip_id=s.trip_id,
            trip_name=s.trip_name,
            expense_id=s.expense_id,
            description=s.description,
            expense_date=s.expense_date,
            category_key=s.category_key,
            amount=_money(db, s.amount, s.currency),
        )
        for s in lines
    ]


# --- Budgets


class BudgetOut(BaseModel):
    category: CategoryRef | None  # null: an overall budget
    currency: str
    limit: MoneyOut
    spent: MoneyOut
    remaining: MoneyOut
    percent: str
    state: Literal["normal", "approaching", "exceeded"]
    alert_percent: int
    effective_from: date
    # Same category, other currencies: never converted, listed as not counted (OD-7).
    not_counted: list[MoneyOut]


class ArchivedBudgetOut(BaseModel):
    category: CategoryRef | None
    currency: str
    ended_from: date
    last_limit: MoneyOut | None


class BudgetsOut(BaseModel):
    month: str
    budgets: list[BudgetOut]
    archived: list[ArchivedBudgetOut]


def _budget_out(
    db: Session, item: budgets.BudgetMonth, refs: dict[uuid.UUID, CategoryRef]
) -> BudgetOut:
    code = item.key.currency
    return BudgetOut(
        category=refs.get(item.key.category_id) if item.key.category_id else None,
        currency=code,
        limit=_money(db, item.limit, code),
        spent=_money(db, item.spent, code),
        remaining=_money(db, item.remaining, code),
        percent=str(item.percent),
        state=item.state,  # type: ignore[arg-type]
        alert_percent=item.row.alert_percent,
        effective_from=item.row.effective_from_month,
        not_counted=[_money(db, v, c) for c, v in sorted(item.other_currencies.items())],
    )


@router.get("/budgets")
def list_budgets(
    month: Annotated[str | None, Query(max_length=7)] = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> BudgetsOut:
    when = _month(month) if month else today_for(user).replace(day=1)
    items = budgets.month(db, user, when)
    ended = budgets.archived(db, user, when)
    refs = _categories(
        db,
        {i.key.category_id for i in items if i.key.category_id}
        | {k.category_id for k, _, _ in ended if k.category_id},
    )
    return BudgetsOut(
        month=when.strftime("%Y-%m"),
        budgets=[_budget_out(db, i, refs) for i in items],
        archived=[
            ArchivedBudgetOut(
                category=refs.get(k.category_id) if k.category_id else None,
                currency=k.currency,
                ended_from=row.effective_from_month,
                last_limit=_money(db, last.amount_minor, k.currency)
                if last and last.amount_minor is not None
                else None,
            )
            for k, row, last in ended
        ],
    )


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID | None = None
    limit: MoneyIn
    from_month: Annotated[str, Field(max_length=7)]
    alert_percent: Annotated[int, Field(ge=1, le=100)] = 80


@router.put("/budgets", dependencies=[write_rate_limit])
def set_budget(
    body: BudgetIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> BudgetsOut:
    """Creates a budget, or changes it from a month on (earlier months keep their limit)."""
    first = _month(body.from_month)
    budgets.set_limit(
        db,
        user,
        category_id=body.category_id,
        amount=body.limit.amount,
        currency=body.limit.currency,
        from_month=first,
        alert_percent=body.alert_percent,
    )
    return list_budgets(first.strftime("%Y-%m"), user, db)


class BudgetEndIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID | None = None
    currency: CurrencyCode
    from_month: Annotated[str, Field(max_length=7)]


@router.post("/budgets/end", dependencies=[write_rate_limit])
def end_budget(
    body: BudgetEndIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> BudgetsOut:
    """Archives a budget from a month on; earlier months still show it."""
    first = _month(body.from_month)
    budgets.end(db, user, category_id=body.category_id, currency=body.currency, from_month=first)
    return list_budgets(first.strftime("%Y-%m"), user, db)


@router.post("/budgets/restore", dependencies=[write_rate_limit])
def restore_budget(
    body: BudgetEndIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> BudgetsOut:
    """Brings an ended budget back from a month on, with its last limit."""
    first = _month(body.from_month)
    budgets.restore(
        db, user, category_id=body.category_id, currency=body.currency, from_month=first
    )
    return list_budgets(first.strftime("%Y-%m"), user, db)


class BudgetDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID | None = None
    currency: CurrencyCode


@router.post("/budgets/delete", dependencies=[write_rate_limit])
def delete_budget(
    body: BudgetDeleteIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> BudgetsOut:
    """Removes a budget set up by mistake (no earlier month uses it). Never touches spending."""
    current = today_for(user).replace(day=1)
    budgets.delete(db, user, category_id=body.category_id, currency=body.currency, current=current)
    return list_budgets(current.strftime("%Y-%m"), user, db)


class BudgetHistoryRow(BaseModel):
    from_month: date
    limit: MoneyOut | None  # null: ended from this month
    alert_percent: int


class BudgetMonthOut(BaseModel):
    month: date
    limit: MoneyOut | None
    spent: MoneyOut


class BudgetDetailOut(BaseModel):
    budget: BudgetOut | None
    history: list[BudgetHistoryRow]
    months: list[BudgetMonthOut]
    transactions: list[TransactionOut]
    # Same category and month, posted in another currency: shown, not counted (OD-7).
    excluded: list[TransactionOut] = []
    # Allocated trip shares that count in this budget (§11.10): not money from an account.
    trip_shares: list[TripShareLine] = []


@router.get("/budgets/detail")
def budget_detail(
    currency: CurrencyCode,
    month: Annotated[str, Query(max_length=7)],
    category_id: uuid.UUID | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> BudgetDetailOut:
    when = _month(month)
    key = budgets.Key(category_id, currency)
    rows = budgets.history(db, user, key)
    if not rows:
        raise ApiError(404, "not_found", "No such budget")
    current = next((b for b in budgets.month(db, user, when) if b.key == key), None)
    refs = _categories(db, {category_id} if category_id else set())
    return BudgetDetailOut(
        budget=_budget_out(db, current, refs) if current else None,
        history=[
            BudgetHistoryRow(
                from_month=r.effective_from_month,
                limit=_money(db, r.amount_minor, currency) if r.amount_minor is not None else None,
                alert_percent=r.alert_percent,
            )
            for r in rows
        ],
        months=[
            BudgetMonthOut(
                month=m,
                limit=_money(db, limit, currency) if limit is not None else None,
                spent=_money(db, spent, currency),
            )
            for m, limit, spent in budgets.recent_months(db, user, key, when)
        ],
        transactions=_transactions_out(db, budgets.contributing(db, user, key, when)),
        excluded=_transactions_out(db, budgets.contributing(db, user, key, when, excluded=True)),
        trip_shares=_share_lines(db, budgets.contributing_shares(db, user, key, when)),
    )


# --- Insights


class Totals(BaseModel):
    income: MoneyOut
    expenses: MoneyOut  # spending: everyday spending plus trip shares, per the view
    net: MoneyOut
    transactions: int
    # The two parts of `expenses`, so a duplicate can be noticed by eye (§11.7).
    non_trip_expenses: MoneyOut | None = None
    trip_shares: MoneyOut | None = None


def _share_totals(db: Session, lines: list[reporting.ShareLine], code: str) -> list[TripShareTotal]:
    totals: dict[uuid.UUID, list[Any]] = {}
    for s in lines:
        if s.currency == code:
            item = totals.setdefault(s.trip_id, [s.trip_name, 0, 0])
            item[1] += s.amount
            item[2] += 1
    return [
        TripShareTotal(trip_id=i, trip_name=n, amount=_money(db, a, code), count=c)
        for i, (n, a, c) in sorted(totals.items(), key=lambda kv: (-kv[1][1], kv[1][0]))
    ]


class CategoryShare(BaseModel):
    category: CategoryRef | None
    amount: MoneyOut
    percent: str
    previous: MoneyOut


class SeriesPoint(BaseModel):
    bucket: date
    income: MoneyOut
    expenses: MoneyOut


class InsightsOut(BaseModel):
    currency: str | None
    currencies: list[str]
    date_from: date
    date_to: date
    totals: Totals | None
    previous: Totals | None
    categories: list[CategoryShare]
    monthly: list[SeriesPoint]
    daily: list[SeriesPoint]
    grain: Literal["day", "week", "month"]
    trip_allocation: AllocationView = "include"


def _totals(db: Session, flow: reporting.Flows | None, code: str) -> Totals:
    flow = flow or reporting.Flows(code)
    return Totals(
        income=_money(db, flow.income, code),
        expenses=_money(db, flow.expenses, code),
        net=_money(db, flow.net, code),
        transactions=flow.transactions,
        non_trip_expenses=_money(db, flow.non_trip_expenses, code),
        trip_shares=_money(db, flow.trip_shares, code),
    )


def _shares(
    db: Session,
    current: list[reporting.CategoryTotal],
    previous: list[reporting.CategoryTotal],
    code: str,
) -> list[CategoryShare]:
    spend = [c for c in current if c.kind == "expense" and c.currency == code and c.amount > 0]
    before = {
        c.category_id: c.amount for c in previous if c.kind == "expense" and c.currency == code
    }
    total = sum(c.amount for c in spend)
    refs = _categories(db, {c.category_id for c in spend if c.category_id})
    return [
        CategoryShare(
            category=refs.get(c.category_id) if c.category_id else None,
            amount=_money(db, c.amount, code),
            percent=str(reporting.percent(c.amount, total)),
            previous=_money(db, before.get(c.category_id, 0), code),
        )
        for c in sorted(spend, key=lambda c: -c.amount)
    ]


def _points(
    db: Session,
    points: list[reporting.Point],
    code: str,
    start: date,
    end: date,
    grain: str,
) -> list[SeriesPoint]:
    """Every bucket in the period, with zeros where nothing happened, so a chart never joins
    two days across a gap as if the day between had money in it."""
    found = {p.bucket: p for p in points if p.currency == code}
    out = []
    for bucket in reporting.buckets(start, end, grain):
        p = found.get(bucket)
        out.append(
            SeriesPoint(
                bucket=bucket,
                income=_money(db, p.income if p else 0, code),
                expenses=_money(db, p.expenses if p else 0, code),
            )
        )
    return out


def _first_date(user: User) -> date | None:
    """The earliest dated transaction, for "all history"."""
    with snapshot(user) as db:
        first: date | None = db.scalar(
            select(func.min(Transaction.effective_date)).where(Transaction.user_id == user.id)
        )
    return first


def _range(
    user: User, date_from: date | None, date_to: date | None, all_history: bool = False
) -> tuple[date, date]:
    today = today_for(user)
    end = date_to or today
    check_date(date_from)
    check_date(end)
    if all_history:
        first = _first_date(user)
        start = max(min(first, end), MIN_DATE) if first else end.replace(day=1)
        return start, end
    start = date_from or end.replace(day=1)
    if start > end:
        raise ApiError(422, "validation_error", "The start is after the end")
    if (end - start).days > 366 * 10:
        raise ApiError(422, "validation_error", "Choose at most ten years")
    return start, end


@router.get("/insights", dependencies=[heavy_read])
def insights(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    currency: CurrencyCode | None = None,
    account_id: uuid.UUID | None = None,
    all_history: bool = False,
    trip_allocation: AllocationView = "include",
    user: User = Depends(require_active_user),
) -> InsightsOut:
    start, end = _range(user, date_from, date_to, all_history)
    accounts = (account_id,) if account_id else ()
    view = trip_allocation
    with snapshot(user) as db:
        scope = reporting.Scope(start, end, accounts, trip_allocation=view)
        flows = reporting.flows(db, user, scope)
        # Busiest currency first; never added together.
        codes = sorted(flows, key=lambda c: (-(flows[c].expenses + flows[c].income), c))
        code = currency if currency else (codes[0] if codes else None)
        if code is None:
            return InsightsOut(
                currency=None,
                currencies=[],
                date_from=start,
                date_to=end,
                totals=None,
                previous=None,
                categories=[],
                monthly=[],
                daily=[],
                grain="day",
            )
        scoped = reporting.Scope(start, end, accounts, currencies=(code,), trip_allocation=view)
        previous = reporting.flows(db, user, scoped.previous())
        # Insights count income and expense entries, like Home (transfers and the like aren't
        # spending or income, so they aren't counted here; Reports count every transaction).
        entries = ("income", "expense")
        counted = reporting.flows(
            db, user, reporting.Scope(start, end, accounts, types=entries, currencies=(code,))
        )
        counted_before = reporting.flows(
            db,
            user,
            reporting.Scope(
                scoped.previous().date_from,
                scoped.previous().date_to,
                accounts,
                types=entries,
                currencies=(code,),
            ),
        )
        months_from = budgets.add_months(end.replace(day=1), -11)
        days = (end - start).days
        grain: Literal["day", "week", "month"] = (
            "day" if days <= 62 else "week" if days <= 400 else "month"
        )
        return InsightsOut(
            currency=code,
            currencies=codes if code in codes else [code, *codes],
            date_from=start,
            date_to=end,
            totals=_totals(db, flows.get(code), code).model_copy(
                update={"transactions": counted[code].transactions if code in counted else 0}
            ),
            previous=_totals(db, previous.get(code), code).model_copy(
                update={
                    "transactions": counted_before[code].transactions
                    if code in counted_before
                    else 0
                }
            ),
            categories=_shares(
                db,
                reporting.by_category(db, user, scoped),
                reporting.by_category(db, user, scoped.previous()),
                code,
            ),
            monthly=_points(
                db,
                reporting.series(
                    db,
                    user,
                    reporting.Scope(
                        months_from, end, accounts, currencies=(code,), trip_allocation=view
                    ),
                    "month",
                ),
                code,
                months_from,
                end,
                "month",
            ),
            daily=_points(db, reporting.series(db, user, scoped, grain), code, start, end, grain),
            grain=grain,
            trip_allocation=view,
        )


# --- Spending Intelligence (FINANCIAL_RULES.md §16.6)


def _window(window: intelligence.Window) -> dict[str, Any]:
    return {"from": window.start, "to": window.end, "days": window.days}


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _intelligence(
    db: Session,
    user: User,
    *,
    as_of: date,
    period: str,
    custom: intelligence.Window | None,
    code: str | None,
    accounts: tuple[uuid.UUID, ...],
    categories: tuple[uuid.UUID, ...],
    types: tuple[str, ...],
    view: str,
    trend_end: date,
) -> dict[str, Any]:
    current, previous = intelligence.window_for(period, as_of, custom)
    comparisons = intelligence.comparison_windows(as_of)
    first_of_period = current.start.replace(day=1)
    earliest = min(
        [
            current.start,
            previous.start,
            *(w.start for _, _, w in comparisons),
            intelligence.shift_months(
                first_of_period, -intelligence.UNUSUAL_MONTHS * intelligence.UNUSUAL_MAX_STEP
            ),
            intelligence.shift_months(current.end.replace(day=1), -5),
            intelligence.shift_months(as_of.replace(day=1), -intelligence.PACE_MONTHS),
            intelligence.shift_months(trend_end.replace(day=1), -11),
        ]
    )
    scope = reporting.Scope(earliest, as_of, accounts, categories, types, (), view)
    every = reporting.day_lines(db, user, scope)
    first = intelligence.first_activity(db, user, every)
    in_period = [line for line in every if current.holds(line.day)]
    weight: dict[str, int] = {}
    for line in in_period:
        weight[line.currency] = weight.get(line.currency, 0) + line.amount
    codes = sorted(weight, key=lambda c: (-weight[c], c))
    chosen = code or (codes[0] if codes else None)

    notes = []
    if accounts:
        notes.append("account_excludes_trip_shares")
    if categories:
        notes.append("category_hides_income_and_commitments")
    if types == ("income",):
        notes.append("income_only")
    if types == ("expense",):
        notes.append("refunds_excluded")
    if view != "include":
        notes.append(f"trip_{view}")
    if accounts or categories or types:
        notes.append("budgets_ignore_filters")

    base: dict[str, Any] = {
        "as_of": as_of,
        "period": period,
        "window": _window(current),
        "previous": _window(previous),
        "first_activity": first,
        "currency": chosen,
        "currencies": codes if chosen is None or chosen in codes else [chosen, *codes],
        "notes": notes,
        "trend_end": trend_end,
    }
    if chosen is None:
        return base | {"empty": True}

    lines = intelligence.Lines(line for line in every if line.currency == chosen)

    def cash(minor: int) -> MoneyOut:
        return _money(db, minor, chosen)

    rows = intelligence.category_changes(lines, current, previous)
    found = intelligence_categories(db, [r.category_id for r in rows])

    def ref(cid: uuid.UUID | None) -> CategoryRef | None:
        return found.get(cid) if cid else None

    def row_out(row: intelligence.CategoryChange) -> dict[str, Any]:
        return {
            "category": ref(row.category_id),
            "current": cash(row.current),
            "previous": cash(row.previous),
            "change": cash(row.change),
            "percent": _decimal_text(intelligence.percent_change(row.current, row.previous)),
        }

    def compare(key: str, now: intelligence.Window, then: intelligence.Window) -> dict[str, Any]:
        a, b = lines.spent(now), lines.spent(then)
        reason = intelligence.percent_reason(b, then, first)
        return {
            "key": key,
            "current": _window(now),
            "previous": _window(then),
            "current_amount": a,
            "previous_amount": b,
            "percent": intelligence.percent_change(a, b) if reason is None else None,
            "reason": reason,
        }

    def compare_out(item: dict[str, Any]) -> dict[str, Any]:
        return item | {
            "current_amount": cash(item["current_amount"]),
            "previous_amount": cash(item["previous_amount"]),
            "difference": cash(item["current_amount"] - item["previous_amount"]),
            "percent": _decimal_text(item["percent"]),
        }

    selected = compare("selected", current, previous)
    spent = lines.spent(current)
    parts = lines.by_class(current)
    parts_before = lines.by_class(previous)
    pace = intelligence.pace(lines, as_of, first)
    marks = intelligence.highlights(lines, rows, current, previous, first)
    budget_list = intelligence.budget_rows(db, user, as_of, chosen)
    commitments = (
        None
        if categories or types
        else intelligence.commitments(db, user, lines, current, as_of, chosen)
    )
    trips_on = view != "exclude" and not accounts and types != ("income",)
    trips = intelligence.trips(lines, current, previous) if trips_on else None
    cash_moved = (
        reporting.movements(
            db, user, reporting.Scope(current.start, current.end, currencies=(chosen,))
        ).get(chosen)
        if trips_on
        else None
    )
    heat_ids = [r.category_id for r in rows if r.current > 0][:6]
    heat = intelligence.heatmap(lines, heat_ids, current.end)

    insights = intelligence.key_insights(
        comparison=selected,
        rows=rows,
        parts=parts,
        total=spent,
        pace_data=pace,
        budget_list=budget_list,
        commitment=commitments,
    )
    budget_refs = intelligence_categories(db, [b.key.category_id for b in budget_list])
    money_fields = ("current", "previous", "difference", "limit", "total")

    def insight_out(item: dict[str, Any]) -> dict[str, Any]:
        out = dict(item)
        for name in money_fields:
            if name in out and out[name] is not None:
                out[name] = cash(out[name])
        if "percent" in out:
            out["percent"] = _decimal_text(out["percent"])
        if "category_id" in out:
            cid = out.pop("category_id")
            out["category"] = found.get(cid) or budget_refs.get(cid)
        return out

    def highlight(row: intelligence.CategoryChange | None) -> dict[str, Any] | None:
        return row_out(row) if row else None

    total_previous = sum(max(r.previous, 0) for r in rows)
    return base | {
        "empty": False,
        "totals": {
            "income": cash(lines.earned(current)),
            "spent": cash(spent),
            "net": cash(lines.earned(current) - spent),
        },
        "comparisons": [compare_out(compare(k, a, b)) for k, a, b in comparisons],
        "selected": compare_out(selected),
        "pace": {
            **pace,
            **{
                k: cash(pace[k]) if pace[k] is not None else None
                for k in (
                    "so_far",
                    "average_daily",
                    "historical_daily",
                    "difference_daily",
                    "projected",
                )
            },
            "percent": _decimal_text(pace["percent"]),
        },
        "months": [
            {
                "month": p["month"],
                "partial": p["partial"],
                **{
                    k: cash(p[k])
                    for k in ("income", "spent", "scheduled", "variable", "financed", "trip")
                },
            }
            for p in intelligence.month_points(lines, trend_end, as_of)
        ],
        "categories": [
            {
                "category": ref(r.category_id),
                "amount": cash(r.current),
                "percent": str(reporting.percent(r.current, spent)),
                "previous": cash(r.previous),
            }
            for r in rows
            if r.current > 0
        ],
        "changes": [row_out(r) for r in rows],
        "previous_spent": cash(total_previous),
        "highlights": {
            "highest": highlight(marks["highest"]),
            "fastest": highlight(marks["fastest"]),
            "largest_increase": highlight(marks["largest_increase"]),
            "largest_decrease": highlight(marks["largest_decrease"]),
            "new": [row_out(r) for r in marks["new"]],
            "unusual": [
                {
                    **row_out(u["row"]),
                    "typical": cash(u["typical"]),
                    "months": u["months"],
                }
                for u in marks["unusual"]
            ],
        },
        "heatmap": {
            "months": heat["months"],
            "rows": [
                {"category": ref(cid), "values": [cash(v) for v in heat["rows"][cid]]}
                for cid in heat_ids
            ],
        },
        "weekdays": [
            {
                "weekday": w["weekday"],
                "days": w["days"],
                "total": cash(w["total"]),
                "average": cash(w["average"]),
            }
            for w in intelligence.weekdays(lines, current)
        ],
        "daily": [
            {"day": day, "amount": cash(lines.by_day(current).get(day, 0))}
            for day in reporting.buckets(current.start, current.end, "day")
        ]
        if current.days <= 62
        else None,
        "fixed": {
            "scheduled": cash(parts.get("scheduled", 0)),
            "subscriptions": cash(parts.get("subscription", 0)),
            "variable": cash(parts.get("variable", 0)),
            "financed": cash(parts.get("financed", 0)),
            "trip": cash(parts.get("trip", 0)),
            "previous_scheduled": cash(
                parts_before.get("scheduled", 0) + parts_before.get("subscription", 0)
            ),
            "previous_variable": cash(parts_before.get("variable", 0)),
        },
        "budgets": [
            {
                "category": budget_refs.get(b.key.category_id) if b.key.category_id else None,
                "limit": cash(b.limit),
                "spent": cash(b.spent),
                "percent": str(b.percent),
                "state": b.state,
                "alert_percent": b.row.alert_percent,
            }
            for b in budget_list
        ],
        "commitments": commitments
        and {
            **commitments,
            **{
                k: cash(commitments[k])
                for k in (
                    "income",
                    "spent",
                    "net",
                    "confirmed",
                    "upcoming_total",
                    "upcoming_recurring",
                    "upcoming_installments",
                    "installment_monthly",
                    "month_income",
                    "month_spent",
                    "due_this_month",
                    "remaining_after_known",
                    "usual_confirmed",
                )
            },
            "installment_income_ratio": _decimal_text(commitments["installment_income_ratio"]),
        },
        "trips": trips
        and {
            "everyday": cash(trips["everyday"]),
            "shares": cash(trips["shares"]),
            "total": cash(trips["total"]),
            "percent": _decimal_text(trips["percent"]),
            "previous_shares": cash(trips["previous_shares"]),
            "cash_paid": cash(cash_moved.trips_out) if cash_moved else None,
            "cash_received": cash(cash_moved.trips_in) if cash_moved else None,
            "by_trip": [
                {"id": t["id"], "name": t["name"], "amount": cash(t["amount"])}
                for t in trips["by_trip"]
            ],
        },
        "insights": [insight_out(i) for i in insights],
    }


def intelligence_categories(
    db: Session, ids: list[uuid.UUID | None]
) -> dict[uuid.UUID, CategoryRef]:
    return _categories(db, {cid for cid in ids if cid})


@router.get("/analytics/intelligence", dependencies=[heavy_read])
def spending_intelligence(
    request: Request,
    as_of: date | None = None,
    period: Literal["mtd", "qtd", "half", "ytd", "rolling6", "last12", "custom"] = "mtd",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    currency: CurrencyCode | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    type: Literal["income", "expense"] | None = None,
    trip_allocation: AllocationView = "include",
    trend_end: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    equivalent: CurrencyCode | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Comparisons, pace, and category analysis, per currency. Display only."""
    today = recurring.ensure(db, user)  # so expected payments exist before they're read
    db.commit()
    check_date(as_of)
    moment = min(as_of or today, today)
    custom = None
    if period == "custom":
        if date_from is None or date_to is None:
            raise ApiError(422, "validation_error", "A custom period needs both dates")
        start, end = _range(user, date_from, min(date_to, moment))
        custom = intelligence.Window(start, end)
        moment = end
    last_month = moment.replace(day=1)
    trend = min(date.fromisoformat(f"{trend_end}-01"), last_month) if trend_end else last_month
    accounts = (account_id,) if account_id else ()
    categories = (category_id,) if category_id else ()
    types = (type,) if type else ()
    with snapshot(user) as read:
        out = _intelligence(
            read,
            user,
            as_of=moment,
            period=period,
            custom=custom,
            code=currency,
            accounts=accounts,
            categories=categories,
            types=types,
            view=trip_allocation,
            trend_end=trend,
        )
        current, previous = intelligence.window_for(period, moment, custom)
        lines = reporting.day_lines(
            read,
            user,
            reporting.Scope(
                min(current.start, previous.start),
                moment,
                accounts,
                categories,
                types,
                (),
                trip_allocation,
            ),
        )
    if equivalent is None or out.get("empty"):
        return out | {"equivalent": None}

    def totals(window: intelligence.Window) -> list[tuple[str, int]]:
        found: dict[str, int] = {}
        for line in lines:
            if line.kind == "expense" and window.holds(line.day):
                found[line.currency] = found.get(line.currency, 0) + line.amount
        return sorted(found.items())

    # The same rates value both periods, so a change here is spending, never the exchange rate.
    now_parts, now_total = equivalents.spending_in(
        db, gateway(request), subtotals=totals(current), target=equivalent, today=today
    )
    then_parts, then_total = equivalents.spending_in(
        db, gateway(request), subtotals=totals(previous), target=equivalent, today=today
    )

    def indicative(value: Decimal | None) -> MoneyOut | None:
        return (
            MoneyOut(amount=format(value, "f"), currency=equivalent) if value is not None else None
        )

    change = None
    if now_total is not None and then_total is not None and then_total > 0:
        change = ((now_total - then_total) * 100 / then_total).quantize(Decimal("0.1"))
    return out | {
        "equivalent": {
            "target": equivalent,
            "method": "latest_reference_both_periods",
            "current": indicative(now_total),
            "previous": indicative(then_total),
            "percent": _decimal_text(change),
            "complete": now_total is not None and (then_total is not None or not then_parts),
            "is_stale": any(p.is_stale for p in [*now_parts, *then_parts]),
            "parts": [
                {
                    "currency": p.currency,
                    "spent": _money(db, p.spent_minor, p.currency),
                    "status": p.status,
                    "equivalent": indicative(p.value),
                    "rate": _decimal_text(p.rate.normalize()) if p.rate is not None else None,
                    "provider": p.provider,
                    "upstream": p.upstream,
                    "rate_date": p.rate_date,
                }
                for p in now_parts
            ],
        }
    }


# --- Reports


class Filters(BaseModel):
    date_from: date
    date_to: date
    type: str | None
    account_id: uuid.UUID | None
    category_id: uuid.UUID | None
    currency: str | None
    q: str | None
    grain: GrainName | None = None  # the breakdown; chosen from the length when not given
    trip_allocation: AllocationView = "include"


class BalanceRow(BaseModel):
    opening: MoneyOut
    initial_balances: MoneyOut
    income: MoneyOut
    expenses: MoneyOut
    transfers_in: MoneyOut
    transfers_out: MoneyOut
    conversions_in: MoneyOut
    conversions_out: MoneyOut
    adjustments: MoneyOut
    debts_in: MoneyOut
    debts_out: MoneyOut
    trips_in: MoneyOut  # cash received back from trips: never income
    trips_out: MoneyOut  # cash paid into trips: never spending (the share is)
    refunds: MoneyOut  # money back for purchases: reduces spending, never income
    closing: MoneyOut
    reconciles: bool


class CurrencyReport(BaseModel):
    currency: str
    totals: Totals
    previous: Totals
    balances: BalanceRow | None
    categories: list[CategoryShare]
    grain: Literal["day", "week", "month"]
    series: list[SeriesPoint]
    trips: list[TripShareTotal] = []  # the trip shares in `totals.expenses`, by trip


class OccurrenceRow(BaseModel):
    name: str
    kind: str
    window_start: date
    window_end: date
    state: Literal["expected", "pending", "overdue", "confirmed", "skipped"]
    expected: MoneyOut | None
    actual: MoneyOut | None


class DebtRow(BaseModel):
    id: uuid.UUID
    counterparty: str
    direction: str
    original: MoneyOut
    repaid_in_period: MoneyOut
    remaining: MoneyOut


class ExportLimits(BaseModel):
    """Rows a PDF (and Print, which prints that PDF) or a CSV can hold."""

    pdf_rows: int
    csv_rows: int


class ReportOut(BaseModel):
    generated_at: datetime
    time_zone: str
    filters: Filters
    balances_applicable: bool
    by_currency: list[CurrencyReport]
    # Every posted transaction in the period and filters (all types), before the description search.
    transaction_count: int
    # The rows the transaction table holds: the same, narrowed by the description search (q).
    table_count: int = 0
    limits: ExportLimits | None = None
    recurring: list[OccurrenceRow]
    debts: list[DebtRow]


def _grain(start: date, end: date) -> Literal["day", "week", "month"]:
    days = (end - start).days
    return "day" if days <= 31 else "week" if days <= 120 else "month"


def _scope(f: Filters) -> reporting.Scope:
    return reporting.Scope(
        f.date_from,
        f.date_to,
        (f.account_id,) if f.account_id else (),
        (f.category_id,) if f.category_id else (),
        (f.type,) if f.type else (),
        (f.currency,) if f.currency else (),
        f.trip_allocation,
    )


def _report(db: Session, user: User, f: Filters) -> ReportOut:
    scope = _scope(f)
    data = reporting.report(db, user, scope)
    today = today_for(user)
    grain = f.grain or _grain(f.date_from, f.date_to)
    points = reporting.series(db, user, scope, grain)
    codes = sorted(
        set(data.flows) | set(data.movements),
        key=lambda c: (-(data.flows.get(c, reporting.Flows(c)).transactions), c),
    )
    by_currency = []
    for code in codes:
        m = data.movements.get(code)
        balances = (
            BalanceRow(
                **{
                    name: _money(db, getattr(m, name), code)
                    for name in (
                        "opening",
                        "initial_balances",
                        "income",
                        "expenses",
                        "transfers_in",
                        "transfers_out",
                        "conversions_in",
                        "conversions_out",
                        "adjustments",
                        "debts_in",
                        "debts_out",
                        "trips_in",
                        "trips_out",
                        "refunds",
                        "closing",
                    )
                },
                reconciles=m.reconciles,
            )
            if m is not None
            else None
        )
        by_currency.append(
            CurrencyReport(
                currency=code,
                totals=_totals(db, data.flows.get(code), code),
                previous=_totals(db, data.previous.get(code), code),
                balances=balances,
                categories=_shares(db, data.categories, data.previous_categories, code),
                grain=grain,
                series=_points(db, points, code, f.date_from, f.date_to, grain),
                trips=_share_totals(db, data.trip_shares, code),
            )
        )
    recurring = []
    for line in data.occurrences:
        o = line.occurrence
        state = (
            o.status
            if o.status != "pending"
            else (
                "expected"
                if today < o.window_start
                else "pending"
                if today <= o.window_end
                else "overdue"
            )
        )
        recurring.append(
            OccurrenceRow(
                name=line.rule.name,
                kind=line.rule.kind,
                window_start=o.window_start,
                window_end=o.window_end,
                state=state,  # type: ignore[arg-type]
                expected=_money(db, o.expected_amount_minor, o.currency)
                if o.expected_amount_minor is not None
                else None,
                actual=_money(db, line.actual_minor, line.actual_currency)
                if line.actual_minor is not None and line.actual_currency
                else None,
            )
        )
    debts = [
        DebtRow(
            id=d.debt.id,
            counterparty=d.debt.counterparty,
            direction=d.debt.direction,
            original=_money(db, d.debt.original_amount_minor, d.debt.currency),
            repaid_in_period=_money(db, d.repaid_in_period, d.debt.currency),
            remaining=_money(db, d.remaining, d.debt.currency),
        )
        for d in data.debts
    ]
    return ReportOut(
        generated_at=datetime.now(UTC),
        time_zone=user.time_zone,
        filters=f,
        balances_applicable=scope.balances_applicable,
        by_currency=by_currency,
        transaction_count=data.count,
        recurring=recurring,
        debts=debts,
    )


def report_filters(
    user: User = Depends(require_active_user),
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    type: TypeName | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    currency: CurrencyCode | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    all_history: bool = False,
    grain: GrainName | None = None,
    trip_allocation: AllocationView = "include",
) -> Filters:
    start, end = _range(user, date_from, date_to, all_history)
    return Filters(
        date_from=start,
        date_to=end,
        type=type,
        account_id=account_id,
        category_id=category_id,
        currency=currency,
        q=q.strip() if q and q.strip() else None,
        grain=grain,
        trip_allocation=trip_allocation,
    )


@router.get("/reports", dependencies=[heavy_read])
def get_report(
    request: Request,
    f: Filters = Depends(report_filters),
    user: User = Depends(require_active_user),
) -> ReportOut:
    settings: Settings = request.app.state.settings
    with snapshot(user) as db:
        report = _report(db, user, f)
        return report.model_copy(
            update={
                "table_count": transactions.count(db, user, _table_filters(f)),
                "limits": ExportLimits(
                    pdf_rows=settings.pdf_max_rows, csv_rows=settings.csv_max_rows
                ),
            }
        )


def _table_filters(f: Filters) -> transactions.Filters:
    return transactions.Filters(
        type=f.type,
        account_id=f.account_id,
        category_id=f.category_id,
        date_from=f.date_from,
        date_to=f.date_to,
        search=f.q,
        include_voided=False,
        currency=f.currency,
    )


def _rows(db: Session, user: User, f: Filters, cap: int) -> tuple[list[Transaction], bool]:
    """The report's transactions, newest first, up to `cap` (and whether there were more)."""
    filters = _table_filters(f)
    rows: list[Transaction] = []
    cursor: str | None = None
    while len(rows) < cap:
        page, cursor = transactions.history(
            db, user, filters, limit=min(500, cap - len(rows)), cursor=cursor
        )
        rows.extend(page)
        if not cursor:
            return rows, False
    return rows, cursor is not None


class _Names:
    """Category and account names in the export's language."""

    def __init__(self, db: Session, rows: list[Transaction], locale: str) -> None:
        self.t = text(locale)
        self.categories = {
            c.id: c
            for c in db.scalars(
                select(Category).where(
                    Category.id.in_({r.category_id for r in rows if r.category_id})
                )
            )
        }
        ids = {r.account_id for r in rows if r.account_id} | {
            r.counter_account_id for r in rows if r.counter_account_id
        }
        self.accounts = {
            a.id: a
            for a in db.scalars(select(FinancialAccount).where(FinancialAccount.id.in_(ids)))
        }

    def category(self, ref: Any) -> str:
        if ref is None:
            return ""
        if getattr(ref, "name", None):
            return str(ref.name)
        key = getattr(ref, "system_key", None)
        return self.t.get(f"cat_{key}", "") if key else ""

    def account(self, account_id: uuid.UUID | None) -> str:
        account = self.accounts.get(account_id) if account_id else None
        if account is None:
            return ""
        return account.name or self.t.get(f"account_{account.system_key}", "")


def _filters_label(db: Session, user: User, f: Filters, locale: str) -> list[tuple[str, str]]:
    t = text(locale)
    out = []
    if f.type:
        out.append((t["type"], t[f"type_{f.type}"]))
    if f.account_id:
        account = db.scalar(
            select(FinancialAccount).where(
                FinancialAccount.id == f.account_id, FinancialAccount.user_id == user.id
            )
        )
        if account:
            out.append((t["account"], account.name or t.get(f"account_{account.system_key}", "")))
    if f.category_id:
        category = db.scalar(
            select(Category).where(Category.id == f.category_id, Category.user_id == user.id)
        )
        if category:
            out.append((t["category"], category.name or t.get(f"cat_{category.system_key}", "")))
    if f.currency:
        out.append((t["currency"], f.currency))
    if f.q:
        out.append((t["search"], f.q))
    return out


def flow(item: TransactionOut) -> Literal["in", "out"] | None:
    """Money in or out of the person's accounts, as the web app shows it. Transfers and
    conversions between their own accounts are neither."""
    if item.type == "income":
        return "in"
    if item.type in ("expense", "trip_expense_payment", "trip_settlement_paid"):
        return "out"
    if item.type in ("trip_settlement_received", "refund"):
        return "in"
    if item.type == "adjustment":
        return "in" if item.direction == "increase" else "out"
    if item.debt is not None and item.account is not None:
        # Borrowing and being repaid bring money in; lending and repaying send it out.
        owe = item.debt.direction == "i_owe"
        return "in" if (item.type == "debt_start") == owe else "out"
    return None


@router.get("/reports/export.csv", dependencies=[export_limit])
def export_csv(
    request: Request,
    f: Filters = Depends(report_filters),
    locale: Literal["en", "ar", "tr"] = "en",
    user: User = Depends(require_active_user),
) -> Response:
    settings: Settings = request.app.state.settings
    with snapshot(user) as db:
        rows, more = _rows(db, user, f, settings.csv_max_rows)
        if more:
            raise ApiError(
                413,
                "export_too_large",
                "Too many rows for one file; narrow the period",
                {"limit": settings.csv_max_rows},
            )
        names = _Names(db, rows, locale)
        out = _transactions_out(db, rows)
        t = names.t
        zone = _zone(user.time_zone)
        header = [
            "date",
            "type",
            "description",
            "category",
            "account",
            "counter_account",
            "amount",
            "currency",
            "original_amount",
            "original_currency",
            "fx_rate",
            "fx_method",
            "notes",
            "transaction_id",
            "recorded_at",
        ]
        data = []
        for row, item in zip(rows, out, strict=True):
            signed = item.amount.amount
            if flow(item) == "out":
                signed = f"-{signed}"
            data.append(
                [
                    item.effective_date.isoformat(),
                    t.get(f"type_{item.type}", item.type),
                    item.description or "",
                    names.category(item.category),
                    names.account(row.account_id),
                    names.account(row.counter_account_id)
                    or (item.debt.counterparty if item.debt else ""),
                    signed,
                    item.amount.currency,
                    item.original_amount.amount if item.original_amount else "",
                    item.original_amount.currency if item.original_amount else "",
                    item.fx.rate if item.fx else "",
                    item.fx.method if item.fx else "",
                    item.notes or "",
                    str(item.id),
                    item.created_at.astimezone(zone).isoformat(),
                ]
            )
    body = exports.csv_bytes(header, data, text_columns={1, 2, 3, 4, 5, 12})
    name = f"finpilot-report-{f.date_from.isoformat()}-{f.date_to.isoformat()}.csv"
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/reports/trip-shares.csv", dependencies=[export_limit])
def export_trip_shares_csv(
    f: Filters = Depends(report_filters),
    locale: Literal["en", "ar", "tr"] = "en",
    user: User = Depends(require_active_user),
) -> Response:
    """The allocated trip shares counted in the report's spending (§11.10), one row each.

    A separate file from the transactions CSV: a share is not a transaction and moved no money.
    Empty (header only) when the view leaves shares out or an account filter is active.
    """
    t = text(locale)
    with snapshot(user) as db:
        lines = reporting.scope_shares(db, user, _scope(f))
        exponents = money.exponents(db, {s.currency for s in lines})
        data = [
            [
                s.expense_date.isoformat(),
                s.trip_name,
                s.description,
                t.get(f"cat_{s.category_key}", s.category_key),
                money.to_amount(s.amount, exponents[s.currency]),
                s.currency,
                str(s.trip_id),
                str(s.expense_id),
            ]
            for s in lines
        ]
    header = [
        "date", "trip", "description", "category", "your_share", "currency", "trip_id",
        "expense_id",
    ]  # fmt: skip
    body = exports.csv_bytes(header, data, text_columns={1, 2, 3})
    name = f"finpilot-trip-shares-{f.date_from.isoformat()}-{f.date_to.isoformat()}.csv"
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _period_label(f: Filters, locale: str) -> str:
    months = MONTHS[locale]

    def long(d: date) -> str:
        return f"{d.day} {months[d.month - 1]} {d.year}"

    return (
        long(f.date_from)
        if f.date_from == f.date_to
        else f"{long(f.date_from)}{DASH}{long(f.date_to)}"
    )


def _series_label(grain: str, bucket: date, locale: str) -> str:
    if grain == "month":
        return f"{MONTHS[locale][bucket.month - 1]} {bucket.year}"
    return bucket.isoformat()


@router.get("/reports/export.pdf", dependencies=[export_limit])
def export_pdf(
    request: Request,
    f: Filters = Depends(report_filters),
    locale: Literal["en", "ar", "tr"] = "en",
    user: User = Depends(require_active_user),
) -> Response:
    settings: Settings = request.app.state.settings
    t = text(locale)
    with snapshot(user) as db:
        report = _report(db, user, f)
        rows, more = _rows(db, user, f, settings.pdf_max_rows)
        total_rows = transactions.count(db, user, _table_filters(f)) if more else len(rows)
        names = _Names(db, rows, locale)
        items = _transactions_out(db, rows)
        filters = _filters_label(db, user, f, locale)

    def negated(value: MoneyOut) -> MoneyOut:
        # Spending is shown as money out in the reconciliation. A sign, not arithmetic.
        if Decimal(value.amount) == 0:
            return value
        return MoneyOut(amount=f"-{value.amount}", currency=value.currency)

    def n(value: MoneyOut | None) -> str:
        return f"{exports.number(value.amount, locale)} {value.currency}" if value else ""

    currencies = []
    for c in report.by_currency:
        balances = None
        if c.balances:
            b = c.balances
            # Money out is shown negative; movements that didn't happen are left out.
            moves = [
                ("initial_balances", b.initial_balances),
                ("income", b.income),
                ("expenses", negated(b.expenses)),
                ("transfers_in", b.transfers_in),
                ("transfers_out", negated(b.transfers_out)),
                ("conversions_in", b.conversions_in),
                ("conversions_out", negated(b.conversions_out)),
                ("adjustments", b.adjustments),
                ("debts_in", b.debts_in),
                ("debts_out", negated(b.debts_out)),
                ("refunds", b.refunds),
                ("trips_in", b.trips_in),
                ("trips_out", negated(b.trips_out)),
            ]
            balances = [
                (t["opening"], n(b.opening), True),
                *((t[key], n(value), False) for key, value in moves if Decimal(value.amount)),
                (t["closing"], n(b.closing), True),
            ]
        top = max((Decimal(s.amount.amount) for s in c.categories), default=Decimal(0))
        currencies.append(
            {
                "code": c.currency,
                "income": n(c.totals.income),
                "expenses": n(c.totals.expenses),
                "net": n(c.totals.net),
                "net_negative": c.totals.net.amount.startswith("-"),
                "non_trip": n(c.totals.non_trip_expenses),
                "trip_shares": n(c.totals.trip_shares)
                if c.totals.trip_shares and Decimal(c.totals.trip_shares.amount)
                else "",
                "trips": [
                    {"name": s.trip_name, "amount": n(s.amount), "count": s.count} for s in c.trips
                ],
                "count": c.totals.transactions,
                "prev_income": n(c.previous.income),
                "prev_expenses": n(c.previous.expenses),
                "balances": balances,
                "categories": [
                    {
                        "name": names.category(s.category) or t["uncategorized"],
                        "amount": n(s.amount),
                        "share": exports.number(s.percent, locale),
                        "previous": n(s.previous),
                        # Bar length only, for drawing.
                        "width": float(Decimal(s.amount.amount) / top * 100) if top else 0,
                    }
                    for s in c.categories
                ],
                "series_label": t[
                    "month" if c.grain == "month" else "week" if c.grain == "week" else "day"
                ],
                "series": [
                    {
                        "label": _series_label(c.grain, p.bucket, locale),
                        "income": n(p.income),
                        "expenses": n(p.expenses),
                    }
                    for p in c.series
                    if Decimal(p.income.amount) or Decimal(p.expenses.amount)
                ],
            }
        )

    def conversion(item: TransactionOut) -> str:
        if not item.fx or not item.original_amount:
            return ""
        rate = exports.number(item.fx.rate, locale)
        base = f"1 {item.original_amount.currency} = {rate} {item.amount.currency}"
        label = t.get(f"fx_{item.fx.method}", "")
        date_part = (
            f", {item.fx.rate_date.isoformat()}"
            if item.fx.rate_date and item.fx.method == "reference"
            else ""
        )
        return f"{base} · {label}{date_part}"

    table = []
    for row, item in zip(rows, items, strict=True):
        incoming = flow(item) == "in"
        sign = "-" if flow(item) == "out" else ""
        table.append(
            {
                "date": item.effective_date.isoformat(),
                "description": item.description
                or names.category(item.category)
                or t.get(f"type_{item.type}", ""),
                "category": names.category(item.category) or t.get(f"type_{item.type}", ""),
                "account": names.account(row.account_id)
                or (item.debt.counterparty if item.debt else ""),
                "amount": n(
                    MoneyOut(amount=f"{sign}{item.amount.amount}", currency=item.amount.currency)
                ),
                "original": n(item.original_amount),
                "conversion": conversion(item),
                "incoming": incoming,
                "voided": item.status == "reversed",
            }
        )

    def window(o: OccurrenceRow) -> str:
        return (
            o.window_start.isoformat()
            if o.window_start == o.window_end
            else f"{o.window_start.isoformat()}{DASH}{o.window_end.isoformat()}"
        )

    generated = report.generated_at.astimezone(_zone(user.time_zone))
    html = exports.render_html(
        {
            "lang": locale,
            "dir": "rtl" if locale == "ar" else "ltr",
            "t": t,
            "period": _period_label(f, locale),
            "generated": exports.stamp(generated),
            "time_zone": user.time_zone,
            "filters": filters,
            "balances_hidden": not report.balances_applicable,
            "currencies": currencies,
            "rows": table,
            # Said on the first page, so a PDF never looks complete when rows were left out.
            "complete": not more,
            "completeness": (
                t["partial"].format(
                    shown=exports.number(str(len(rows)), locale),
                    total=exports.number(str(total_rows), locale),
                    omitted=exports.number(str(total_rows - len(rows)), locale),
                    limit=exports.number(str(settings.pdf_max_rows), locale),
                )
                if more
                else t["complete"].format(total=exports.number(str(total_rows), locale))
            ),
            "search_note": t["search_note"].format(q=f.q) if f.q else "",
            "allocation": t[f"allocation_{f.trip_allocation}"],
            "recurring": [
                {
                    "name": o.name,
                    "window": window(o),
                    "state": o.state,
                    "state_label": t[f"state_{o.state}"],
                    "expected": n(o.expected),
                    "actual": n(o.actual),
                }
                for o in report.recurring
            ],
            "debts": [
                {
                    "who": d.counterparty,
                    "direction": t[d.direction],
                    "original": n(d.original),
                    "repaid": n(d.repaid_in_period),
                    "remaining": n(d.remaining),
                }
                for d in report.debts
            ],
        }
    )
    pdf = exports.render_pdf(
        html,
        exports.PdfSettings(
            settings.pdf_browser_channel,
            settings.pdf_browser_path,
            settings.pdf_timeout_seconds * 1000,
            settings.pdf_max_concurrent,
        ),
        {
            "dir": "rtl" if locale == "ar" else "ltr",
            "generated": exports.stamp(generated),
            "page": t["page"],
            "of": t["of"],
        },
    )
    name = f"finpilot-report-{f.date_from.isoformat()}-{f.date_to.isoformat()}-{locale}.pdf"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "X-Report-Complete": "false" if more else "true",
            "X-Report-Rows-Included": str(len(rows)),
            "X-Report-Rows-Total": str(total_rows),
        },
    )


def _zone(name: str) -> Any:
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        return UTC
