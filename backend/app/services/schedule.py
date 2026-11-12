"""When recurring money is expected: pure date arithmetic (FINANCIAL_RULES.md R5, R6, R8).

Dates are business dates in the person's time zone and never move when the zone changes. Each
period has one window `[start, end]`:

- **monthly**: days `window_start_day` to `window_end_day` of each month. A day past the month's
  end falls on its last day, so 29 to 31 is 28 February to 28 February in 2027 and 29 February to
  29 February in 2028. A window never spans into the next month.
- **yearly**: the same, in one fixed month. 29 February is 28 February in other years.
- **weekly** / **biweekly**: one day, on the weekday of the start date, every 7 or 14 days.

The first period is the first whose window ends on or after the start date. Periods are numbered
from 1 (the `sequence`), which is how "payment 3 of 10" is known and when a count-limited rule
stops. Nothing here reads the database or the clock.
"""

import calendar
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Plan:
    frequency: str
    start_date: date
    window_start_day: int | None = None
    window_end_day: int | None = None
    month: int | None = None
    end_date: date | None = None
    total: int | None = None


@dataclass(frozen=True)
class Window:
    sequence: int
    start: date
    end: date


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _period(plan: Plan, k: int) -> tuple[date, date]:
    """The window of the k-th period counted from the start date's own period (k ≥ 0)."""
    if plan.frequency in ("weekly", "biweekly"):
        day = plan.start_date + timedelta(days=k * (7 if plan.frequency == "weekly" else 14))
        return day, day
    first = plan.window_start_day or 1
    last = plan.window_end_day or first
    if plan.frequency == "monthly":
        index = plan.start_date.year * 12 + plan.start_date.month - 1 + k
        year, month = divmod(index, 12)
        month += 1
    elif plan.frequency == "yearly":
        if plan.month is None:
            raise ValueError("a yearly plan needs a month")
        year, month = plan.start_date.year + k, plan.month
    else:
        raise ValueError(f"unknown frequency {plan.frequency}")
    return _clamp(year, month, first), _clamp(year, month, last)


def windows(plan: Plan, *, ends_on_or_after: date, starts_on_or_before: date) -> Iterator[Window]:
    """Every window of the plan with end ≥ `ends_on_or_after` and start ≤ `starts_on_or_before`,
    respecting the end date and the total count. Bounded: at most a few thousand periods."""
    offset = 0
    start, end = _period(plan, 0)
    if end < plan.start_date:
        offset = 1  # the start date's own period has already passed
    for sequence, k in enumerate(range(offset, offset + 5000), start=1):
        start, end = _period(plan, k)
        if plan.total is not None and sequence > plan.total:
            return
        if plan.end_date is not None and start > plan.end_date:
            return
        if start > starts_on_or_before:
            return
        if end >= ends_on_or_after:
            yield Window(sequence, start, end)


def status_of(start: date, end: date, today: date) -> str:
    """For a pending occurrence: not open yet, open now, or past without an answer."""
    if today < start:
        return "expected"
    if today <= end:
        return "pending"
    return "overdue"
