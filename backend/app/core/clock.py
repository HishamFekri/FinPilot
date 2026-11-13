"""The app's calendar clock: which day it is, in a given time zone.

Every "today" decision (a transaction's default date, a report's end date, what is due) asks
this module, so one instant decides the date everywhere in a request. Timestamps of events
(created, decided, expires) keep using the real time directly.

Tests pin `now` for a whole run (tests/conftest.py), so a run that crosses midnight can't see
two different days: the tests' TODAY and the app's today come from the same instant. Production
never replaces it.
"""

from datetime import UTC, date, datetime, tzinfo


def now() -> datetime:
    return datetime.now(UTC)


def today(zone: tzinfo = UTC) -> date:
    """The calendar date at this moment in `zone`."""
    return now().astimezone(zone).date()
