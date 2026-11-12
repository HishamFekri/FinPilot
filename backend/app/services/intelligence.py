"""Spending Intelligence: comparisons, pace, and category analysis (FINANCIAL_RULES.md §16.6).

Every figure is a sum of `reporting.day_lines`, which reads the same journal and trip shares as
Reports and Home, so the two can't disagree. Everything is per currency, in integer minor units.
Nothing here is stored, and nothing is a prediction except the one figure labelled an estimate.

Calendar rules, in one place (`shift_months`, `window_for`):

- A comparable period is the same calendar position one month, quarter, half or year earlier. A day
  that doesn't exist there is clamped to that month's last day (31 March goes to 28 or 29 February;
  29 February goes to 28 February of a non-leap year).
- Month to date compares the same elapsed days, never a whole month against a partial one.
- A percentage needs a real base. It's withheld, with the reason, when the comparable spending is
  zero or when the person's history doesn't reach back across the whole comparable period.
"""

from __future__ import annotations

import uuid
from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import RecurringOccurrence, RecurringRule, Transaction, User
from app.services import budgets, installments, reporting

PERIODS = ("mtd", "qtd", "half", "ytd", "rolling6", "last12", "custom")
# A previous amount under this share of its period's spending is too small to take a percentage
# of: going from 1 to 5 isn't "400% growth" in any useful sense.
MIN_BASE_SHARE = Decimal("0.02")
UNUSUAL_RATIO = Decimal("1.5")
UNUSUAL_MIN_SHARE = Decimal("0.05")
UNUSUAL_MONTHS = 6
UNUSUAL_MIN_MONTHS = 3
UNUSUAL_MAX_STEP = 3  # months a period may span and still have earlier ones to compare
PACE_MONTHS = 3
PROJECTION_MIN_DAYS = 3
UPCOMING_DAYS = 30
MAX_INSIGHTS = 5
HIGH_COMMITMENTS_RATIO = Decimal("1.5")


def shift_months(value: date, months: int) -> date:
    """The same day `months` away, clamped to the last day of a shorter month."""
    index = value.year * 12 + value.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def month_last(value: date) -> date:
    return value.replace(day=monthrange(value.year, value.month)[1])


def quarter_first(value: date) -> date:
    return date(value.year, (value.month - 1) // 3 * 3 + 1, 1)


def half_first(value: date) -> date:
    return date(value.year, 1 if value.month <= 6 else 7, 1)


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def holds(self, day: date) -> bool:
        return self.start <= day <= self.end


def _shifted(window: Window, months: int) -> Window:
    return Window(shift_months(window.start, months), shift_months(window.end, months))


def window_for(key: str, as_of: date, custom: Window | None = None) -> tuple[Window, Window]:
    """The selected period and the one it's compared with."""
    if key == "mtd":
        current = Window(as_of.replace(day=1), as_of)
        return current, _shifted(current, -1)
    if key == "qtd":
        current = Window(quarter_first(as_of), as_of)
        return current, _shifted(current, -3)
    if key == "half":
        current = Window(half_first(as_of), as_of)
        return current, _shifted(current, -6)
    if key == "ytd":
        current = Window(date(as_of.year, 1, 1), as_of)
        return current, _shifted(current, -12)
    if key in ("rolling6", "last12"):
        months = 6 if key == "rolling6" else 12
        current = Window(shift_months(as_of, -months) + timedelta(days=1), as_of)
        return current, Window(
            shift_months(as_of, -2 * months) + timedelta(days=1), shift_months(as_of, -months)
        )
    if custom is None:
        raise ValueError("custom needs dates")
    previous = reporting.Scope(custom.start, custom.end).previous()
    return custom, Window(previous.date_from, previous.date_to)


def comparison_windows(as_of: date) -> list[tuple[str, Window, Window]]:
    """Every standing comparison, in the order they're shown."""
    last_complete = (
        as_of if as_of == month_last(as_of) else as_of.replace(day=1) - timedelta(days=1)
    )
    complete = Window(last_complete.replace(day=1), last_complete)
    before = shift_months(complete.start, -1)
    mtd, mtd_before = window_for("mtd", as_of)
    qtd, _ = window_for("qtd", as_of)
    half, half_before = window_for("half", as_of)
    ytd, ytd_before = window_for("ytd", as_of)
    rolling, rolling_before = window_for("rolling6", as_of)
    return [
        ("mtd", mtd, mtd_before),
        ("complete_month", complete, Window(before, month_last(before))),
        ("qtd_previous_quarter", qtd, _shifted(qtd, -3)),
        ("qtd_previous_year", qtd, _shifted(qtd, -12)),
        ("ytd", ytd, ytd_before),
        ("half", half, half_before),
        ("rolling6", rolling, rolling_before),
    ]


def percent_change(current: int, previous: int) -> Decimal | None:
    """Change as a percentage of `previous`, one decimal, or None when that base isn't positive."""
    if previous <= 0:
        return None
    return (Decimal(current - previous) * 100 / Decimal(previous)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


def percent_reason(previous: int, window: Window, first_activity: date | None) -> str | None:
    """Why no percentage is shown, or None when one is meaningful."""
    if first_activity is None or first_activity > window.end:
        return "no_history"
    if first_activity > window.start:
        return "partial_history"
    return "zero_previous" if previous <= 0 else None


# --- Reading the lines


class Lines:
    """One currency's expense and income lines, summed however a section needs."""

    def __init__(self, lines: Iterable[reporting.DayLine]) -> None:
        self.lines = list(lines)
        self.spending = [line for line in self.lines if line.kind == "expense"]
        self.income = [line for line in self.lines if line.kind == "income"]

    def spent(self, window: Window) -> int:
        return sum(line.amount for line in self.spending if window.holds(line.day))

    def earned(self, window: Window) -> int:
        return sum(line.amount for line in self.income if window.holds(line.day))

    def by_class(self, window: Window) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for line in self.spending:
            if window.holds(line.day):
                out[line.klass] += line.amount
        return out

    def by_category(self, window: Window) -> dict[uuid.UUID | None, int]:
        out: dict[uuid.UUID | None, int] = defaultdict(int)
        for line in self.spending:
            if window.holds(line.day):
                out[line.category_id] += line.amount
        return out

    def by_day(self, window: Window) -> dict[date, int]:
        out: dict[date, int] = defaultdict(int)
        for line in self.spending:
            if window.holds(line.day):
                out[line.day] += line.amount
        return out


# --- Sections (all per currency)


def pace(lines: Lines, as_of: date, first_activity: date | None) -> dict[str, Any]:
    """Average daily spending so far this month against the same days of recent months."""
    days = as_of.day
    so_far = lines.spent(Window(as_of.replace(day=1), as_of))
    in_month = monthrange(as_of.year, as_of.month)[1]
    compared_days = 0
    compared_total = 0
    months_used = 0
    first_of_month = as_of.replace(day=1)
    for back in range(1, PACE_MONTHS + 1):
        start = shift_months(first_of_month, -back)
        if first_activity is None or first_activity > start:
            continue
        end = min(start + timedelta(days=days - 1), month_last(start))
        compared_total += lines.spent(Window(start, end))
        compared_days += (end - start).days + 1
        months_used += 1
    average = Decimal(so_far) / days
    historical = Decimal(compared_total) / compared_days if compared_days else None
    remaining = in_month - days
    projected = None
    if days >= PROJECTION_MIN_DAYS and remaining > 0:
        projected = so_far + int((average * remaining).quantize(Decimal(1), ROUND_HALF_UP))
    return {
        "so_far": so_far,
        "days_elapsed": days,
        "days_in_month": in_month,
        "average_daily": int(average.quantize(Decimal(1), ROUND_HALF_UP)),
        "historical_daily": (
            int(historical.quantize(Decimal(1), ROUND_HALF_UP)) if historical is not None else None
        ),
        "difference_daily": (
            int((average - historical).quantize(Decimal(1), ROUND_HALF_UP))
            if historical is not None
            else None
        ),
        "percent": (
            None
            if historical is None or historical <= 0
            else ((average - historical) * 100 / historical).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        ),
        "months_used": months_used,
        "enough_history": months_used > 0,
        "projected": projected,
        "projection_reason": (
            None if projected is not None else "month_over" if remaining == 0 else "too_early"
        ),
    }


@dataclass(frozen=True)
class CategoryChange:
    category_id: uuid.UUID | None
    current: int
    previous: int

    @property
    def change(self) -> int:
        return self.current - self.previous


def category_changes(lines: Lines, current: Window, previous: Window) -> list[CategoryChange]:
    now = lines.by_category(current)
    before = lines.by_category(previous)
    rows = [
        CategoryChange(cid, now.get(cid, 0), before.get(cid, 0))
        for cid in set(now) | set(before)
        if now.get(cid, 0) or before.get(cid, 0)
    ]
    return sorted(rows, key=lambda r: (-r.current, str(r.category_id)))


def _reliable_base(previous: int, previous_total: int) -> bool:
    return previous > 0 and Decimal(previous) >= Decimal(previous_total) * MIN_BASE_SHARE


def highlights(
    lines: Lines,
    rows: list[CategoryChange],
    current: Window,
    previous: Window,
    first_activity: date | None,
) -> dict[str, Any]:
    """The categories worth pointing at. Each holds the exact figures behind it."""
    spending = [r for r in rows if r.current > 0]
    previous_total = sum(max(r.previous, 0) for r in rows)
    comparable = percent_reason(previous_total, previous, first_activity) is None
    growing = [r for r in spending if comparable and _reliable_base(r.previous, previous_total)]
    fastest = max(
        (r for r in growing if r.change > 0),
        key=lambda r: (percent_change(r.current, r.previous) or Decimal(0), str(r.category_id)),
        default=None,
    )
    increases = [r for r in rows if comparable and r.change > 0]
    decreases = [r for r in rows if comparable and r.change < 0]
    new = [r for r in spending if comparable and r.previous <= 0]
    return {
        "highest": spending[0] if spending else None,
        "fastest": fastest,
        "largest_increase": max(increases, key=lambda r: r.change, default=None),
        "largest_decrease": min(decreases, key=lambda r: r.change, default=None),
        "new": new,
        "unusual": unusual(lines, spending, current, first_activity),
    }


def unusual(
    lines: Lines, rows: list[CategoryChange], current: Window, first_activity: date | None
) -> list[dict[str, Any]]:
    """Spending well above the same stretch of earlier periods.

    Like for like: rent paid on the 2nd looks huge per day in a nine-day period, so the first nine
    days are compared with the first nine days of earlier months, never with whole months. Needs
    at least three earlier periods with spending in the category, and the amount has to matter
    here. Periods longer than a quarter don't have enough earlier ones to compare.
    """
    step = (
        (current.end.year - current.start.year) * 12 + current.end.month - current.start.month + 1
    )
    if step > UNUSUAL_MAX_STEP:
        return []
    total = sum(r.current for r in rows)
    out = []
    for row in rows:
        amounts: list[int] = []
        for back in range(1, UNUSUAL_MONTHS + 1):
            window = _shifted(current, -step * back)
            if first_activity is None or first_activity > window.start:
                continue
            amount = lines.by_category(window).get(row.category_id, 0)
            if amount > 0:
                amounts.append(amount)
        if len(amounts) < UNUSUAL_MIN_MONTHS:
            continue
        typical = Decimal(sum(amounts)) / len(amounts)
        if Decimal(row.current) >= typical * UNUSUAL_RATIO and Decimal(row.current) >= (
            Decimal(total) * UNUSUAL_MIN_SHARE
        ):
            out.append(
                {
                    "row": row,
                    "typical": int(typical.quantize(Decimal(1), ROUND_HALF_UP)),
                    "months": len(amounts),
                }
            )
    return sorted(out, key=lambda item: -item["row"].current)


def month_points(
    lines: Lines, end_month: date, as_of: date, count: int = 12
) -> list[dict[str, Any]]:
    """Each month's income, spending and its parts; the month holding `as_of` is marked partial."""
    out = []
    for back in range(count - 1, -1, -1):
        start = shift_months(end_month.replace(day=1), -back)
        last = month_last(start)
        window = Window(start, min(last, as_of))
        if window.end < window.start:
            window = Window(start, start - timedelta(days=1))
        parts = lines.by_class(window) if window.days > 0 else {}
        out.append(
            {
                "month": start,
                "income": lines.earned(window) if window.days > 0 else 0,
                "spent": lines.spent(window) if window.days > 0 else 0,
                "scheduled": parts.get("scheduled", 0) + parts.get("subscription", 0),
                "variable": parts.get("variable", 0),
                "financed": parts.get("financed", 0),
                "trip": parts.get("trip", 0),
                "partial": last > as_of,
            }
        )
    return out


def heatmap(
    lines: Lines, categories: list[uuid.UUID | None], end: date, count: int = 6
) -> dict[str, Any]:
    months = [shift_months(end.replace(day=1), -back) for back in range(count - 1, -1, -1)]
    rows = {
        cid: [lines.by_category(Window(m, min(month_last(m), end))).get(cid, 0) for m in months]
        for cid in categories
    }
    return {"months": months, "rows": rows}


def weekdays(lines: Lines, window: Window) -> list[dict[str, int]]:
    """Average spending per weekday (0 is Monday), over the days of the period, spent on or not."""
    totals = [0] * 7
    counts = [0] * 7
    day = window.start
    while day <= window.end:
        counts[day.weekday()] += 1
        day += timedelta(days=1)
    for when, amount in lines.by_day(window).items():
        totals[when.weekday()] += amount
    return [
        {
            "weekday": i,
            "total": totals[i],
            "days": counts[i],
            "average": (
                int((Decimal(totals[i]) / counts[i]).quantize(Decimal(1), ROUND_HALF_UP))
                if counts[i]
                else 0
            ),
        }
        for i in range(7)
    ]


# --- Commitments


def confirmed_obligations(db: Session, user: User, window: Window, code: str) -> int:
    """What was actually posted for recurring expenses and debt payments in the period."""
    total = 0
    scope = reporting.Scope(window.start, window.end, currencies=(code,))
    for line in reporting.occurrences(db, user, scope):
        if (
            line.occurrence.status == "confirmed"
            and line.rule.kind != "income"
            and line.actual_minor is not None
            and line.actual_currency == code
            and window.holds(line.occurrence.window_start)
        ):
            total += line.actual_minor
    return total


def _scheduled(
    db: Session, user: User, as_of: date, code: str, views: list[installments.PlanView]
) -> list[tuple[date, int | None, str]]:
    """Pending expected payments due by the horizon, an installment shown once when a recurring
    rule already expects the same payment (the same rule as Home's timeline)."""
    horizon = as_of + timedelta(days=UPCOMING_DAYS)
    pending = list(
        db.execute(
            select(RecurringOccurrence, RecurringRule)
            .join(RecurringRule, RecurringRule.id == RecurringOccurrence.rule_id)
            .where(
                RecurringOccurrence.user_id == user.id,
                RecurringOccurrence.status == "pending",
                RecurringOccurrence.currency == code,
                RecurringRule.kind != "income",
                RecurringOccurrence.window_start <= horizon,
            )
        ).all()
    )
    out: list[tuple[date, int | None, str]] = [
        (o.window_start, o.expected_amount_minor, "recurring") for o, _ in pending
    ]
    _, upcoming = installments.overview(db, user, views)
    for item in upcoming:
        due = item.state.item.due_date
        if item.plan.currency != code or due > horizon:
            continue
        duplicate = any(
            (rule.debt_id == item.plan.debt_id and abs((o.window_start - due).days) <= 7)
            or (
                o.expected_amount_minor == item.amount_minor
                and abs((o.window_start - due).days) <= 3
            )
            for o, rule in pending
        )
        if not duplicate:
            out.append((due, item.amount_minor, "installment"))
    return out


def commitments(
    db: Session, user: User, lines: Lines, window: Window, as_of: date, code: str
) -> dict[str, Any]:
    """Actual figures for the period, and scheduled future payments kept apart from them."""
    views = installments.list_for(db, user)
    overview, _ = installments.overview(db, user, views)
    currency = next((c for c in overview if c.currency == code), None)
    scheduled = _scheduled(db, user, as_of, code, views)
    month_end = month_last(as_of)
    this_month = Window(as_of.replace(day=1), as_of)
    known = [s for s in scheduled if s[1] is not None]
    due_this_month = sum(s[1] or 0 for s in known if s[0] <= month_end)
    income = lines.earned(this_month)
    spent = lines.spent(this_month)
    ratio = None
    if currency and currency.average_income_minor:
        ratio = (
            Decimal(currency.monthly_minor) * 100 / Decimal(currency.average_income_minor)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    # What was confirmed in each of the last three complete months: the yardstick for "unusual".
    usual: list[int] = []
    first = as_of.replace(day=1)
    for back in range(1, 4):
        start = shift_months(first, -back)
        usual.append(confirmed_obligations(db, user, Window(start, month_last(start)), code))
    return {
        "income": lines.earned(window),
        "spent": lines.spent(window),
        "net": lines.earned(window) - lines.spent(window),
        "confirmed": confirmed_obligations(db, user, window, code),
        "upcoming_total": sum(s[1] or 0 for s in known),
        "upcoming_recurring": sum(s[1] or 0 for s in known if s[2] == "recurring"),
        "upcoming_installments": sum(s[1] or 0 for s in known if s[2] == "installment"),
        "upcoming_unknown": len(scheduled) - len(known),
        "upcoming_days": UPCOMING_DAYS,
        "installment_monthly": currency.monthly_minor if currency else 0,
        "installment_income_ratio": ratio,
        "income_months": currency.income_months if currency else 0,
        "month_income": income,
        "month_spent": spent,
        "due_this_month": due_this_month,
        "remaining_after_known": income - spent - due_this_month,
        "usual_confirmed": int(sum(usual) / 3) if any(usual) else 0,
    }


def trips(lines: Lines, window: Window, previous: Window) -> dict[str, Any]:
    by_trip: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
    for line in lines.spending:
        if line.klass == "trip" and window.holds(line.day) and line.trip_id and line.trip_name:
            by_trip[(line.trip_id, line.trip_name)] += line.amount
    trip_total = sum(by_trip.values())
    total = lines.spent(window)
    return {
        "everyday": total - trip_total,
        "shares": trip_total,
        "total": total,
        "percent": reporting.percent(trip_total, total) if total > 0 else None,
        "previous_shares": lines.by_class(previous).get("trip", 0),
        "by_trip": sorted(
            ({"id": k[0], "name": k[1], "amount": v} for k, v in by_trip.items()),
            key=lambda item: -int(item["amount"]),
        ),
    }


def budget_rows(db: Session, user: User, as_of: date, code: str) -> list[budgets.BudgetMonth]:
    rows = [b for b in budgets.month(db, user, as_of) if b.key.currency == code]
    return sorted(rows, key=lambda b: -b.percent)


def key_insights(
    *,
    comparison: dict[str, Any],
    rows: list[CategoryChange],
    parts: dict[str, int],
    total: int,
    pace_data: dict[str, Any],
    budget_list: list[budgets.BudgetMonth],
    commitment: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The most useful few, most important first. Every one is arithmetic on figures shown here."""
    out: list[dict[str, Any]] = []
    change = comparison["current_amount"] - comparison["previous_amount"]
    if comparison["percent"] is not None and change != 0:
        out.append(
            {
                "code": "spending_up" if change > 0 else "spending_down",
                "kind": "comparison",
                "current": comparison["current_amount"],
                "previous": comparison["previous_amount"],
                "difference": abs(change),
                "percent": abs(comparison["percent"]),
            }
        )
        driver = (
            max(rows, key=lambda r: r.change, default=None)
            if change > 0
            else min(rows, key=lambda r: r.change, default=None)
        )
        if driver and driver.change * change > 0:
            out.append(
                {
                    "code": "driver_up" if change > 0 else "driver_down",
                    "kind": "category",
                    "category_id": driver.category_id,
                    "current": driver.current,
                    "previous": driver.previous,
                    "difference": abs(driver.change),
                }
            )
    for budget in budget_list:
        if budget.percent >= budget.row.alert_percent:
            out.append(
                {
                    "code": "budget_over" if budget.spent > budget.limit else "budget_near",
                    "kind": "budget",
                    "category_id": budget.key.category_id,
                    "current": budget.spent,
                    "limit": budget.limit,
                    "percent": budget.percent,
                }
            )
            break
    subscriptions = parts.get("subscription", 0)
    if subscriptions > 0 and total > 0:
        out.append(
            {
                "code": "subscriptions_share",
                "kind": "share",
                "current": subscriptions,
                "total": total,
                "percent": reporting.percent(subscriptions, total),
            }
        )
    if pace_data["percent"] is not None and abs(pace_data["percent"]) >= 5:
        out.append(
            {
                "code": "pace_up" if pace_data["percent"] > 0 else "pace_down",
                "kind": "pace",
                "current": pace_data["average_daily"],
                "previous": pace_data["historical_daily"],
                "percent": abs(pace_data["percent"]),
                "months": pace_data["months_used"],
            }
        )
    if (
        commitment
        and commitment["usual_confirmed"] > 0
        and Decimal(commitment["upcoming_total"])
        >= Decimal(commitment["usual_confirmed"]) * HIGH_COMMITMENTS_RATIO
    ):
        out.append(
            {
                "code": "commitments_high",
                "kind": "commitments",
                "current": commitment["upcoming_total"],
                "previous": commitment["usual_confirmed"],
                "days": UPCOMING_DAYS,
            }
        )
    return out[:MAX_INSIGHTS]


# --- Helpers the route uses


def first_activity(db: Session, user: User, lines: Iterable[reporting.DayLine]) -> date | None:
    """The first day the person recorded spending or income: where their history starts."""
    first: date | None = db.scalar(
        select(func.min(Transaction.effective_date)).where(
            Transaction.user_id == user.id,
            Transaction.type.in_(("income", "expense", "refund")),
            Transaction.status == "posted",
        )
    )
    for line in lines:
        if line.klass == "trip" and (first is None or line.day < first):
            first = line.day
    return first
