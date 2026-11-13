"""The calendar clock (app/core/clock.py) and the run-wide pin that keeps tests on one day.

Regression (CI, commit 907ce97): the tests' TODAY was computed when the modules were imported,
at 23:5x UTC, and the app read the real clock while the tests ran, after midnight. New records
got the next day's date, outside every TODAY-bounded report range, so reports lost debts,
transactions and CSV rows.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import clock
from tests import test_fx, test_reports
from tests.session_clock import SESSION_NOW


def test_the_app_and_the_tests_agree_on_today_for_the_whole_run() -> None:
    assert clock.now() == SESSION_NOW
    assert clock.today() == test_fx.TODAY == test_reports.TODAY == SESSION_NOW.date()


def test_today_is_the_date_in_the_users_zone_at_that_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late = datetime(2026, 11, 12, 23, 50, tzinfo=UTC)
    monkeypatch.setattr(clock, "now", lambda: late)
    assert clock.today().isoformat() == "2026-11-12"
    assert clock.today(ZoneInfo("Europe/Istanbul")).isoformat() == "2026-11-13"
    assert clock.today(ZoneInfo("America/New_York")).isoformat() == "2026-11-12"


def test_a_run_crossing_midnight_keeps_one_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """What CI hit: the instant used for "today" must not move with the wall clock."""
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 11, 12, 23, 59, tzinfo=UTC))
    before = clock.today()
    # Real time passing doesn't matter: only the clock decides the day.
    assert clock.today() == before
    later = datetime(2026, 11, 12, 23, 59, tzinfo=UTC) + timedelta(minutes=2)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert clock.today() == before + timedelta(days=1)
