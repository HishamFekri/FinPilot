"""Spending Intelligence (FINANCIAL_RULES.md §16.6): calendar rules, and representative workflows
through the real API. Every total has to agree with Reports, because they read the same lines."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.services import intelligence as intel
from app.services.intelligence import Window, comparison_windows, percent_reason, shift_months
from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_installments import _pay, _plan
from tests.test_links import _category_id, _setup
from tests.test_trips import API, Person, _expense, _ok, _trip, people  # noqa: F401 - fixtures

PATH = "/analytics/intelligence"
AS_OF = date(2025, 5, 20)


def _spend(
    person: Person,
    account: dict[str, Any],
    amount: str,
    when: date,
    category: str = "groceries",
    kind: str = "expense",
) -> Any:
    body = {
        "type": kind,
        "account_id": account["id"],
        "amount": {"amount": amount, "currency": account["currency"]},
        "category_id": _category_id(person, category),
        "effective_date": when.isoformat(),
    }
    return _ok(person.post("/transactions", body), 201)


def _read(person: Person, **params: Any) -> Any:
    return _ok(person.get(PATH, **params))


def _comparison(body: Any, key: str) -> Any:
    return next(c for c in body["comparisons"] if c["key"] == key)


def _span(window: Window) -> tuple[str, str]:
    return window.start.isoformat(), window.end.isoformat()


# --- The calendar


@pytest.mark.parametrize(
    ("value", "months", "expected"),
    [
        (date(2024, 3, 31), -1, date(2024, 2, 29)),  # leap year
        (date(2023, 3, 31), -1, date(2023, 2, 28)),
        (date(2024, 2, 29), -12, date(2023, 2, 28)),
        (date(2025, 1, 15), -1, date(2024, 12, 15)),
        (date(2024, 12, 31), 2, date(2025, 2, 28)),
    ],
)
def test_months_shift_and_clamp(value: date, months: int, expected: date) -> None:
    assert shift_months(value, months) == expected


def test_month_to_date_compares_the_same_elapsed_days() -> None:
    now, then = intel.window_for("mtd", date(2025, 3, 14))
    assert _span(now) == ("2025-03-01", "2025-03-14")
    assert _span(then) == ("2025-02-01", "2025-02-14")
    # The 31st against a shorter month stops at that month's end.
    assert _span(intel.window_for("mtd", date(2024, 3, 31))[1]) == ("2024-02-01", "2024-02-29")
    assert _span(intel.window_for("mtd", date(2023, 3, 31))[1]) == ("2023-02-01", "2023-02-28")


def test_a_completed_month_is_compared_with_the_whole_month_before() -> None:
    windows = {k: (a, b) for k, a, b in comparison_windows(date(2024, 3, 15))}
    now, then = windows["complete_month"]
    assert _span(now) == ("2024-02-01", "2024-02-29")
    assert _span(then) == ("2024-01-01", "2024-01-31")
    # On a month's last day, that month is complete.
    windows = {k: (a, b) for k, a, b in comparison_windows(date(2024, 3, 31))}
    assert _span(windows["complete_month"][0]) == ("2024-03-01", "2024-03-31")


def test_year_to_date_handles_a_leap_day() -> None:
    now, then = intel.window_for("ytd", date(2024, 2, 29))
    assert _span(now) == ("2024-01-01", "2024-02-29")
    assert _span(then) == ("2023-01-01", "2023-02-28")


@pytest.mark.parametrize(
    ("as_of", "first"),
    [
        (date(2025, 2, 10), date(2025, 1, 1)),
        (date(2025, 6, 30), date(2025, 4, 1)),
        (date(2025, 7, 1), date(2025, 7, 1)),
        (date(2025, 12, 31), date(2025, 10, 1)),
    ],
)
def test_calendar_quarters(as_of: date, first: date) -> None:
    assert intel.quarter_first(as_of) == first


def test_quarter_to_date_against_last_quarter_and_last_year() -> None:
    windows = {k: (a, b) for k, a, b in comparison_windows(date(2025, 5, 15))}
    assert _span(windows["qtd_previous_quarter"][0]) == ("2025-04-01", "2025-05-15")
    assert _span(windows["qtd_previous_quarter"][1]) == ("2025-01-01", "2025-02-15")
    assert _span(windows["qtd_previous_year"][1]) == ("2024-04-01", "2024-05-15")


def test_half_year_and_rolling_six_months_are_different_things() -> None:
    windows = {k: (a, b) for k, a, b in comparison_windows(date(2024, 8, 31))}
    assert _span(windows["half"][0]) == ("2024-07-01", "2024-08-31")
    assert _span(windows["half"][1]) == ("2024-01-01", "2024-02-29")
    assert _span(windows["rolling6"][0]) == ("2024-03-01", "2024-08-31")
    assert _span(windows["rolling6"][1]) == ("2023-09-01", "2024-02-29")


def test_a_percentage_needs_a_real_base() -> None:
    period = Window(date(2025, 4, 1), date(2025, 4, 20))
    assert intel.percent_change(150, 100) == 50
    assert intel.percent_change(150, 0) is None
    assert percent_reason(100, period, date(2020, 1, 1)) is None
    assert percent_reason(0, period, date(2020, 1, 1)) == "zero_previous"
    assert percent_reason(100, period, date(2025, 4, 10)) == "partial_history"
    assert percent_reason(0, period, date(2025, 5, 1)) == "no_history"
    assert percent_reason(0, period, None) == "no_history"


# --- Through the API


def test_month_to_date_uses_only_the_comparable_days(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice)
    _spend(alice, bank, "100.00", date(2025, 1, 5))  # history reaches back
    _spend(alice, bank, "120.00", date(2025, 5, 10))
    _spend(alice, bank, "80.00", date(2025, 4, 10))
    _spend(alice, bank, "999.00", date(2025, 4, 25))  # after April 20: not comparable
    body = _read(alice, as_of=AS_OF.isoformat())
    mtd = _comparison(body, "mtd")
    assert mtd["current"]["to"] == "2025-05-20" and mtd["previous"]["to"] == "2025-04-20"
    assert mtd["current_amount"]["amount"] == "120.00"
    assert mtd["previous_amount"]["amount"] == "80.00"
    assert mtd["difference"]["amount"] == "40.00"
    assert mtd["percent"] == "50.0"
    complete = _comparison(body, "complete_month")
    assert complete["current_amount"]["amount"] == "1079.00"  # all of April


def test_zero_previous_spending_invents_no_percentage(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice)
    _spend(alice, bank, "10.00", date(2025, 1, 5))
    _spend(alice, bank, "60.00", date(2025, 5, 2))
    mtd = _comparison(_read(alice, as_of=AS_OF.isoformat()), "mtd")
    assert mtd["percent"] is None and mtd["reason"] == "zero_previous"
    assert mtd["difference"]["amount"] == "60.00"


def test_missing_history_is_said_rather_than_treated_as_zero(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice)
    _spend(alice, bank, "60.00", date(2025, 5, 2))
    body = _read(alice, as_of=AS_OF.isoformat())
    assert _comparison(body, "mtd")["reason"] == "no_history"
    assert _comparison(body, "ytd")["reason"] == "no_history"
    assert body["pace"]["enough_history"] is False
    assert body["pace"]["historical_daily"] is None


def test_the_month_of_first_activity_is_partial(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice)
    _spend(alice, bank, "60.00", date(2025, 4, 15))
    _spend(alice, bank, "70.00", date(2025, 5, 2))
    assert _comparison(_read(alice, as_of=AS_OF.isoformat()), "mtd")["reason"] == "partial_history"


def test_today_follows_the_persons_time_zone(people: list[Person]) -> None:
    alice, bob, *_ = people
    for person, zone in ((alice, "Pacific/Kiritimati"), (bob, "Etc/GMT+12")):
        _setup(person)
        _ok(person.c.patch(f"{API}/auth/me", json={"time_zone": zone}, headers=person.h(False)))
        body = _read(person)
        assert body["as_of"] == datetime.now(ZoneInfo(zone)).date().isoformat()


def test_currencies_stay_apart_and_an_equivalent_uses_one_set_of_rates(
    people: list[Person],
) -> None:
    alice, *_ = people
    eur = _setup(alice)
    try_body = {"name": "Lira", "type": "wallet", "currency": "TRY",
                "opening_balance": {"amount": "99999.00", "currency": "TRY"}}  # fmt: skip
    lira = _ok(alice.post("/accounts", try_body), 201)
    today = TODAY
    previous_day = shift_months(today.replace(day=1), -1)
    _spend(alice, eur, "10.00", shift_months(today.replace(day=1), -3))
    for account, amount in ((eur, "100.00"), (lira, "5631.00")):
        _spend(alice, account, amount, today.replace(day=1))
        _spend(alice, account, amount, previous_day)
    body = _read(alice, currency="EUR", equivalent="EUR")
    assert body["totals"]["spent"] == {"amount": "100.00", "currency": "EUR"}
    assert set(body["currencies"]) == {"EUR", "TRY"}
    other = _read(alice, currency="TRY")
    assert other["totals"]["spent"]["amount"] == "5631.00"
    equivalent = body["equivalent"]
    assert equivalent["current"]["amount"] == "200.00"  # 100 EUR + 5631 TRY at 56.31
    assert equivalent["method"] == "latest_reference_both_periods"
    # The same spending in both periods reads as no change, whatever the rate did since.
    assert equivalent["percent"] == "0.0"
    rate = next(p for p in equivalent["parts"] if p["currency"] == "TRY")
    assert rate["rate"] and rate["rate_date"] and rate["provider"]


def test_a_missing_rate_means_no_combined_figure(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    yen = _ok(alice.post("/accounts", {"name": "Yen", "type": "wallet", "currency": "JPY",
        "opening_balance": {"amount": "9000", "currency": "JPY"}}), 201)  # fmt: skip
    _spend(alice, eur, "5.00", TODAY)
    _spend(alice, yen, "500", TODAY)
    equivalent = _read(alice, currency="EUR", equivalent="EUR")["equivalent"]
    assert equivalent["current"] is None and equivalent["complete"] is False
    assert {p["currency"]: p["status"] for p in equivalent["parts"]}["JPY"] == "unavailable"


def test_trip_shares_can_be_included_or_left_out(people: list[Person]) -> None:
    alice, bob, *_ = people
    eur = _setup(alice)
    _setup(bob)
    _spend(alice, eur, "40.00", TODAY)
    trip = _trip(alice, members=[bob])
    expense = _expense(alice, trip, alice, "90.00", [alice, bob])
    assert expense
    body = _read(alice)
    assert body["totals"]["spent"]["amount"] == "85.00"  # 40 + a 45 share
    assert body["trips"]["shares"]["amount"] == "45.00"
    assert body["trips"]["everyday"]["amount"] == "40.00"
    assert body["trips"]["percent"] == "52.9"
    assert [t["name"] for t in body["trips"]["by_trip"]] == ["Lisbon"]
    assert body["fixed"]["trip"]["amount"] == "45.00"
    without = _read(alice, trip_allocation="exclude")
    assert without["totals"]["spent"]["amount"] == "40.00" and without["trips"] is None
    only = _read(alice, trip_allocation="only")
    assert only["totals"]["spent"]["amount"] == "45.00"
    # Bob's figures are his own: his share only, none of Alice's spending.
    assert _read(bob)["totals"]["spent"]["amount"] == "45.00"


def _rule(person: Person, account: dict[str, Any], name: str, subscription: bool) -> Any:
    today = TODAY
    body = {
        "kind": "expense", "name": name, "currency": "EUR", "frequency": "monthly",
        "amount": {"amount": "30.00", "currency": "EUR"}, "account_id": account["id"],
        "category_id": _category_id(person, "groceries"), "window_start_day": 1,
        "window_end_day": 31, "start_date": today.replace(day=1).isoformat(),
        "is_subscription": subscription,
    }  # fmt: skip
    return _ok(person.post("/recurring/rules", body), 201)


def _confirm_open(person: Person, rule: dict[str, Any]) -> None:
    items = _ok(person.get("/recurring/occurrences"))
    items = items["items"] if isinstance(items, dict) else items
    open_item = next(o for o in items if o["rule_id"] == rule["id"] and o["status"] == "pending")
    _ok(person.post(f"/recurring/occurrences/{open_item['id']}/confirm",
                    {"amount": {"amount": "30.00", "currency": "EUR"}}))  # fmt: skip


def test_fixed_and_variable_come_from_the_rule_not_the_category(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    plain = _rule(alice, eur, "Gym", subscription=False)
    streaming = _rule(alice, eur, "Streaming", subscription=True)
    _confirm_open(alice, plain)
    _confirm_open(alice, streaming)
    # The same category as both rules, but no rule: variable.
    _spend(alice, eur, "12.00", TODAY)
    body = _read(alice)
    fixed = body["fixed"]
    assert fixed["scheduled"]["amount"] == "30.00"
    assert fixed["subscriptions"]["amount"] == "30.00"
    assert fixed["variable"]["amount"] == "12.00"
    assert body["totals"]["spent"]["amount"] == "72.00"
    assert body["commitments"]["confirmed"]["amount"] == "60.00"


def test_installment_principal_is_never_spending_and_nothing_counts_twice(
    people: list[Person],
) -> None:
    alice, *_ = people
    eur = _setup(alice, opening="5000.00")
    plan = _plan(alice, eur, item_price="1000.00", down_payment="200.00", installment_count=4,
                 first_due_date=(TODAY + timedelta(days=5)).isoformat())  # fmt: skip
    before = _read(alice)
    assert before["totals"]["spent"]["amount"] == "1000.00"  # the full price, once
    assert before["fixed"]["financed"]["amount"] == "1000.00"
    assert before["fixed"]["variable"]["amount"] == "0.00"
    _pay(alice, plan, eur, principal="200.00", interest="15.00")
    after = _read(alice)
    assert after["totals"]["spent"]["amount"] == "1015.00"  # the principal adds nothing
    assert after["fixed"]["scheduled"]["amount"] == "15.00"
    assert after["fixed"]["financed"]["amount"] == "1000.00"


def test_refunds_corrections_and_voids_net_out(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    voided = _spend(alice, eur, "70.00", TODAY)
    _ok(alice.c.post(f"{API}/transactions/{voided['id']}/void",
                     headers={**alice.h(False), "If-Match": f'"{voided["version"]}"'}))  # fmt: skip
    assert _read(alice).get("totals", {}).get("spent", {"amount": "0.00"})["amount"] == "0.00"
    corrected = _spend(alice, eur, "50.00", TODAY)
    match = {**alice.h(False), "If-Match": f'"{corrected["version"]}"'}
    change = {"amount": {"amount": "20.00", "currency": "EUR"}}
    _ok(alice.c.patch(f"{API}/transactions/{corrected['id']}", json=change, headers=match))
    bought = _spend(alice, eur, "100.00", TODAY)
    _ok(alice.post(f"/transactions/{bought['id']}/refunds",
                   {"amount": {"amount": "30.00", "currency": "EUR"}}), 201)  # fmt: skip
    body = _read(alice)
    assert body["totals"]["spent"]["amount"] == "90.00"
    assert body["fixed"]["variable"]["amount"] == "90.00"


def test_every_total_agrees_with_reports_and_insights(people: list[Person]) -> None:
    alice, bob, *_ = people
    eur = _setup(alice)
    _setup(bob)
    _spend(alice, eur, "33.00", TODAY, "groceries")
    _spend(alice, eur, "12.50", TODAY, "shopping")
    _spend(alice, eur, "900.00", TODAY, "salary", kind="income")
    trip = _trip(alice, members=[bob])
    _expense(alice, trip, alice, "20.00", [alice, bob])
    first = TODAY.replace(day=1).isoformat()
    body = _read(alice, currency="EUR")
    report = _ok(alice.get("/reports", **{"from": first, "to": TODAY.isoformat()}))
    row = next(c for c in report["by_currency"] if c["currency"] == "EUR")
    insights = _ok(alice.get("/insights", currency="EUR"))
    assert body["totals"]["spent"] == row["totals"]["expenses"] == insights["totals"]["expenses"]
    assert body["totals"]["income"] == row["totals"]["income"] == insights["totals"]["income"]
    assert body["totals"]["net"] == insights["totals"]["net"]
    assert {c["amount"]["amount"] for c in body["categories"]} == {
        c["amount"]["amount"] for c in insights["categories"]
    }
    # The parts split the total without overlap.
    fixed = body["fixed"]
    parts = ("scheduled", "subscriptions", "variable", "financed", "trip")
    assert sum(float(fixed[p]["amount"]) for p in parts) == float(body["totals"]["spent"]["amount"])


def test_the_trend_can_go_back_and_marks_the_partial_month(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    _spend(alice, eur, "45.00", date(2024, 2, 29))
    _spend(alice, eur, "10.00", AS_OF)
    body = _read(alice, as_of=AS_OF.isoformat(), currency="EUR")
    assert len(body["months"]) == 12
    assert body["months"][-1]["month"] == "2025-05-01" and body["months"][-1]["partial"] is True
    earlier = _read(alice, as_of=AS_OF.isoformat(), trend_end="2024-03", currency="EUR")
    february = next(m for m in earlier["months"] if m["month"] == "2024-02-01")
    assert february["spent"]["amount"] == "45.00" and february["partial"] is False
    # Never past the selected date.
    assert _read(alice, as_of=AS_OF.isoformat(), trend_end="2030-01")["trend_end"] == "2025-05-01"


def test_pace_is_an_average_of_comparable_days_and_the_projection_is_arithmetic(
    people: list[Person],
) -> None:
    alice, *_ = people
    eur = _setup(alice)
    _spend(alice, eur, "1.00", date(2025, 1, 1))  # history starts before the months compared
    _spend(alice, eur, "100.00", date(2025, 2, 5))
    _spend(alice, eur, "100.00", date(2025, 3, 5))
    _spend(alice, eur, "200.00", date(2025, 4, 5))
    _spend(alice, eur, "999.00", date(2025, 4, 28))  # beyond the 10th: not comparable
    _spend(alice, eur, "300.00", date(2025, 5, 7))
    pace = _read(alice, as_of="2025-05-10", currency="EUR")["pace"]
    assert pace["so_far"]["amount"] == "300.00" and pace["days_elapsed"] == 10
    assert pace["average_daily"]["amount"] == "30.00"
    assert pace["months_used"] == 3
    # (100 + 100 + 200) over 30 comparable days
    assert pace["historical_daily"]["amount"] == "13.33"
    assert pace["difference_daily"]["amount"] == "16.67"
    assert pace["projected"]["amount"] == "930.00"  # 300 + 30 x 21 days left


def test_a_new_month_has_too_little_to_project(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    _spend(alice, eur, "10.00", date(2025, 5, 1))
    pace = _read(alice, as_of="2025-05-02", currency="EUR")["pace"]
    assert pace["projected"] is None and pace["projection_reason"] == "too_early"


def test_category_highlights_show_figures_and_ignore_tiny_bases(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    _spend(alice, eur, "10.00", date(2025, 1, 5))
    # April 1-20
    _spend(alice, eur, "500.00", date(2025, 4, 3), "groceries")
    _spend(alice, eur, "300.00", date(2025, 4, 3), "shopping")
    _spend(alice, eur, "1.00", date(2025, 4, 3), "transportation")
    # May 1-20
    _spend(alice, eur, "600.00", date(2025, 5, 3), "groceries")  # +20%
    _spend(alice, eur, "100.00", date(2025, 5, 3), "shopping")  # -200
    _spend(alice, eur, "40.00", date(2025, 5, 3), "transportation")  # +3900%, from almost nothing
    _spend(alice, eur, "25.00", date(2025, 5, 3), "travel")  # new
    marks = _read(alice, as_of=AS_OF.isoformat(), currency="EUR")["highlights"]
    assert marks["highest"]["current"]["amount"] == "600.00"
    assert marks["fastest"]["category"]["system_key"] == "groceries"
    assert marks["fastest"]["percent"] == "20.0"
    assert marks["largest_increase"]["category"]["system_key"] == "groceries"
    assert marks["largest_increase"]["change"]["amount"] == "100.00"
    assert marks["largest_decrease"]["category"]["system_key"] == "shopping"
    assert [n["category"]["system_key"] for n in marks["new"]] == ["travel"]


def test_unusual_spending_needs_a_real_history(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    for month in (1, 2, 3, 4):
        _spend(alice, eur, "100.00", date(2025, month, 10))
    _spend(alice, eur, "500.00", date(2025, 5, 3))
    body = _read(alice, as_of=AS_OF.isoformat(), currency="EUR")
    unusual = body["highlights"]["unusual"]
    assert len(unusual) == 1 and unusual[0]["months"] == 3  # January was only partly recorded
    assert unusual[0]["typical"]["amount"] == "100.00"
    assert unusual[0]["current"]["amount"] == "500.00"


def test_a_bill_paid_early_in_the_month_is_not_unusual(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    _spend(alice, eur, "1.00", date(2024, 12, 1))
    for month in (1, 2, 3, 4, 5):
        _spend(alice, eur, "1800.00", date(2025, month, 2), "housing")
    body = _read(alice, as_of="2025-05-09", currency="EUR")
    assert body["highlights"]["unusual"] == []


def test_key_insights_are_few_and_carry_their_figures(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    _spend(alice, eur, "10.00", date(2025, 1, 5))
    _spend(alice, eur, "100.00", date(2025, 4, 3), "groceries")
    _spend(alice, eur, "150.00", date(2025, 5, 3), "groceries")
    insights = _read(alice, as_of=AS_OF.isoformat(), currency="EUR")["insights"]
    assert 1 <= len(insights) <= 5
    first = insights[0]
    assert first["code"] == "spending_up"
    assert first["current"]["amount"] == "150.00" and first["percent"] == "50.0"
    assert insights[1]["code"] == "driver_up"
    assert insights[1]["category"]["system_key"] == "groceries"


def test_filters_are_consistent_and_say_what_they_cant_cover(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    second = _ok(alice.post("/accounts", {"name": "Two", "type": "bank", "currency": "EUR",
        "opening_balance": {"amount": "500.00", "currency": "EUR"}}), 201)  # fmt: skip
    _spend(alice, eur, "11.00", TODAY, "groceries")
    _spend(alice, second, "22.00", TODAY, "shopping")
    by_account = _read(alice, account_id=second["id"])
    assert by_account["totals"]["spent"]["amount"] == "22.00"
    assert "account_excludes_trip_shares" in by_account["notes"] and by_account["trips"] is None
    by_category = _read(alice, category_id=_category_id(alice, "groceries"))
    assert by_category["totals"]["spent"]["amount"] == "11.00"
    assert by_category["commitments"] is None
    assert "category_hides_income_and_commitments" in by_category["notes"]


def test_commitments_keep_actual_and_scheduled_apart(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice, opening="5000.00")
    _spend(alice, eur, "1000.00", TODAY, "salary", kind="income")
    _spend(alice, eur, "100.00", TODAY)
    _plan(alice, eur, item_price="400.00", down_payment="0.00", installment_count=4,
          first_due_date=(TODAY + timedelta(days=5)).isoformat())  # fmt: skip
    data = _read(alice)["commitments"]
    assert data["income"]["amount"] == "1000.00"
    assert data["spent"]["amount"] == "500.00"
    assert data["net"]["amount"] == "500.00"
    assert data["upcoming_installments"]["amount"] == "100.00"
    assert data["installment_monthly"]["amount"] == "100.00"


def test_it_is_private_and_needs_a_session(people: list[Person]) -> None:
    alice, bob, *_ = people
    eur = _setup(alice)
    _setup(bob)
    _spend(alice, eur, "77.00", TODAY)
    assert _read(bob).get("empty") is True
    from fastapi.testclient import TestClient  # local: this test alone signs nobody in

    with TestClient(alice.c.app) as stranger:
        assert stranger.get(f"{API}{PATH}").status_code == 401
