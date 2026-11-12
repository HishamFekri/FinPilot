"""M5.4 to M5.6: recurring rules and occurrences, confirmation, and personal debts.

Users sign in through the real Google routes against a fake Google (MOCKED, tests only).
"""

import threading
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    JournalLine,
    RecurringOccurrence,
    Transaction,
    User,
)
from app.services import recurring
from app.services.accounts import today_for
from app.services.schedule import Plan, Window, status_of, windows
from tests.auth_harness import sign_in, write_headers
from tests.google_fakes import FakeGoogle

API = "/api/v1"


# --- The schedule (pure dates)


def _all(plan: Plan, since: date, until: date) -> list[tuple[int, str, str]]:
    return [
        (w.sequence, w.start.isoformat(), w.end.isoformat())
        for w in windows(plan, ends_on_or_after=since, starts_on_or_before=until)
    ]


def test_month_end_windows_land_on_the_last_day() -> None:
    plan = Plan("monthly", date(2027, 1, 1), 29, 31)
    got = _all(plan, date(2027, 1, 1), date(2027, 4, 30))
    assert got == [
        (1, "2027-01-29", "2027-01-31"),
        (2, "2027-02-28", "2027-02-28"),
        (3, "2027-03-29", "2027-03-31"),
        (4, "2027-04-29", "2027-04-30"),
    ]
    leap = _all(Plan("monthly", date(2028, 2, 1), 29, 31), date(2028, 2, 1), date(2028, 2, 29))
    assert leap == [(1, "2028-02-29", "2028-02-29")]


def test_a_window_like_the_25th_to_the_28th() -> None:
    plan = Plan("monthly", date(2026, 11, 1), 25, 28)
    assert _all(plan, date(2026, 11, 1), date(2027, 1, 31)) == [
        (1, "2026-11-25", "2026-11-28"),
        (2, "2026-12-25", "2026-12-28"),
        (3, "2027-01-25", "2027-01-28"),
    ]


def test_a_start_after_this_months_window_begins_next_month() -> None:
    plan = Plan("monthly", date(2026, 11, 29), 25, 28)
    assert _all(plan, date(2026, 11, 29), date(2026, 12, 31))[0] == (1, "2026-12-25", "2026-12-28")
    # A start inside the window counts this month.
    inside = Plan("monthly", date(2026, 11, 26), 25, 28)
    assert _all(inside, date(2026, 11, 26), date(2026, 11, 30)) == [(1, "2026-11-25", "2026-11-28")]


def test_yearly_29_february_is_28_february_in_other_years() -> None:
    plan = Plan("yearly", date(2027, 1, 1), 29, 29, month=2)
    assert _all(plan, date(2027, 1, 1), date(2029, 12, 31)) == [
        (1, "2027-02-28", "2027-02-28"),
        (2, "2028-02-29", "2028-02-29"),
        (3, "2029-02-28", "2029-02-28"),
    ]


def test_weekly_and_every_two_weeks_keep_the_start_weekday() -> None:
    start = date(2026, 11, 4)  # a Wednesday
    weekly = _all(Plan("weekly", start), start, start + timedelta(days=21))
    assert [w[1] for w in weekly] == ["2026-11-04", "2026-11-11", "2026-11-18", "2026-11-25"]
    biweekly = _all(Plan("biweekly", start), start, start + timedelta(days=30))
    assert [w[1] for w in biweekly] == ["2026-11-04", "2026-11-18", "2026-12-02"]


def test_installments_stop_after_the_count_and_rules_after_the_end_date() -> None:
    plan = Plan("monthly", date(2026, 11, 1), 5, 5, total=10)
    got = _all(plan, date(2026, 11, 1), date(2030, 1, 1))
    assert len(got) == 10 and got[-1] == (10, "2027-08-05", "2027-08-05")
    ended = Plan("monthly", date(2026, 11, 1), 5, 5, end_date=date(2027, 1, 4))
    assert [w[1] for w in _all(ended, date(2026, 11, 1), date(2030, 1, 1))] == [
        "2026-11-05",
        "2026-12-05",
    ]


def test_later_windows_keep_their_sequence_numbers() -> None:
    plan = Plan("monthly", date(2026, 1, 1), 5, 5, total=10)
    got = _all(plan, date(2026, 6, 1), date(2026, 7, 31))
    assert got == [(6, "2026-06-05", "2026-06-05"), (7, "2026-07-05", "2026-07-05")]


def test_status_is_derived_from_today() -> None:
    w = Window(1, date(2026, 11, 25), date(2026, 11, 28))
    assert status_of(w.start, w.end, date(2026, 11, 24)) == "expected"
    assert status_of(w.start, w.end, date(2026, 11, 25)) == "pending"
    assert status_of(w.start, w.end, date(2026, 11, 28)) == "pending"
    assert status_of(w.start, w.end, date(2026, 11, 29)) == "overdue"


# --- API helpers


def _keyed(csrf: str, key: str | None = None) -> dict[str, str]:
    return {**write_headers(csrf), "Idempotency-Key": key or str(uuid.uuid4())}


def _setup(client: TestClient, csrf: str, currency: str = "TRY") -> dict[str, dict[str, Any]]:
    assert (
        client.post(
            f"{API}/setup", json={"currency": currency}, headers=write_headers(csrf)
        ).status_code
        == 200
    )
    return {a["system_key"]: a for a in client.get(f"{API}/accounts").json()}


def _category(client: TestClient, key: str) -> str:
    return str(
        next(c["id"] for c in client.get(f"{API}/categories").json() if c["system_key"] == key)
    )


def _today(factory: sessionmaker[Session]) -> date:
    with factory() as db:
        user = db.scalars(select(User)).first()
        assert user is not None
        return today_for(user)


def _rule(client: TestClient, csrf: str, account: dict[str, Any], **over: Any) -> Any:
    body: dict[str, Any] = {
        "kind": "income",
        "name": "Salary",
        "amount": {"amount": "35000.00", "currency": "TRY"},
        "currency": "TRY",
        "account_id": account["id"],
        "category_id": _category(client, "salary"),
        "frequency": "monthly",
        "window_start_day": 1,
        "window_end_day": 31,
        **over,
    }
    return client.post(f"{API}/recurring/rules", json=body, headers=_keyed(csrf))


def _open_rule(
    client: TestClient, csrf: str, account: dict[str, Any], today: date, **over: Any
) -> Any:
    """A monthly rule whose window is open today (the 1st to the 31st)."""
    return _rule(client, csrf, account, start_date=today.replace(day=1).isoformat(), **over)


def _occurrences(client: TestClient) -> list[dict[str, Any]]:
    return list(client.get(f"{API}/recurring/occurrences").json())


def _confirm(
    client: TestClient,
    csrf: str,
    occurrence: dict[str, Any],
    amount: str,
    key: str | None = None,
    **extra: Any,
) -> Any:
    body = {
        "amount": {
            "amount": amount,
            "currency": occurrence["expected"]["currency"] if occurrence["expected"] else "TRY",
        },
        **extra,
    }
    return client.post(
        f"{API}/recurring/occurrences/{occurrence['id']}/confirm",
        json=body,
        headers=_keyed(csrf, key),
    )


def _balance(client: TestClient, account: dict[str, Any]) -> str:
    return str(client.get(f"{API}/accounts/{account['id']}").json()["balance"]["amount"])


def _month_totals(client: TestClient, today: date) -> list[tuple[str, str, str]]:
    rows = client.get(f"{API}/summary", params={"month": today.strftime("%Y-%m")}).json()[
        "currencies"
    ]
    return [(r["currency"], r["income"]["amount"], r["expenses"]["amount"]) for r in rows]


def _journal_balanced(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        sums: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        for entry_id, currency, amount in db.execute(
            select(JournalLine.entry_id, JournalLine.currency, JournalLine.amount_minor)
        ):
            sums[(entry_id, currency)] += amount
    assert all(v == 0 for v in sums.values())


# --- Rules and occurrences


def test_expected_money_never_touches_balances_or_totals(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    today = _today(factory)
    created = _open_rule(browser, csrf, bank, today)
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["next"]["state"] == "pending" and rule["next"]["expected"]["amount"] == "35000.00"
    items = _occurrences(browser)
    assert 1 <= len(items) <= 3  # this month and up to 35 days ahead
    assert _balance(browser, bank) == "0.00"
    assert _month_totals(browser, today) == []
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0


def test_generation_is_idempotent_even_when_concurrent(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    _rule(browser, csrf, bank, frequency="weekly", window_start_day=None, window_end_day=None)
    first = len(_occurrences(browser))
    errors: list[BaseException] = []

    def read() -> None:
        try:
            with factory() as db:
                user = db.scalars(select(User)).first()
                assert user is not None
                recurring.ensure(db, user)
                db.commit()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=read) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(_occurrences(browser)) == first
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(RecurringOccurrence)) == first


def test_confirming_records_what_really_happened_exactly_once(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    today = _today(factory)
    _open_rule(browser, csrf, bank, today)
    occurrence = _occurrences(browser)[0]
    key = str(uuid.uuid4())
    first = _confirm(browser, csrf, occurrence, "34500.00", key=key)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["occurrence"]["status"] == "confirmed"
    assert body["occurrence"]["expected"]["amount"] == "35000.00"
    assert body["occurrence"]["actual"]["amount"] == "34500.00"
    assert body["transaction"]["type"] == "income"
    assert body["transaction"]["description"] == "Salary"
    replay = _confirm(browser, csrf, occurrence, "34500.00", key=key)
    assert replay.json() == body and replay.headers.get("Idempotent-Replayed") == "true"
    again = _confirm(browser, csrf, occurrence, "34500.00")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")
    assert _balance(browser, bank) == "34500.00"
    assert _month_totals(browser, today) == [("TRY", "34500.00", "0.00")]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
        assert db.scalar(select(Transaction.created_via)) == "recurring"


def test_concurrent_confirmations_post_once(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    _open_rule(browser, csrf, bank, _today(factory))
    occurrence = _occurrences(browser)[0]
    statuses: list[int] = []
    lock = threading.Lock()

    def attempt() -> None:
        response = _confirm(browser, csrf, occurrence, "35000.00")
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [200, 409, 409, 409, 409]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
    _journal_balanced(factory)


def test_skip_and_not_yet_record_nothing(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    _open_rule(browser, csrf, bank, _today(factory))
    occurrence = _occurrences(browser)[0]
    assert any(i["id"] == occurrence["id"] for i in browser.get(f"{API}/recurring/upcoming").json())
    rested = browser.post(
        f"{API}/recurring/occurrences/{occurrence['id']}/not-yet", headers=write_headers(csrf)
    )
    assert rested.json()["status"] == "pending" and rested.json()["remind"] is False
    assert all(i["id"] != occurrence["id"] for i in browser.get(f"{API}/recurring/upcoming").json())
    skipped = browser.post(
        f"{API}/recurring/occurrences/{occurrence['id']}/skip", headers=write_headers(csrf)
    )
    assert skipped.json()["status"] == "skipped"
    assert _confirm(browser, csrf, occurrence, "1.00").json()["code"] == "invalid_state"
    assert (
        browser.post(
            f"{API}/recurring/occurrences/{occurrence['id']}/skip", headers=write_headers(csrf)
        ).status_code
        == 409
    )
    assert _balance(browser, bank) == "0.00"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0


def test_voiding_the_posted_transaction_reopens_the_occurrence(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    _open_rule(browser, csrf, bank, _today(factory))
    occurrence = _occurrences(browser)[0]
    txn = _confirm(browser, csrf, occurrence, "35000.00").json()["transaction"]
    voided = browser.post(
        f"{API}/transactions/{txn['id']}/void",
        headers={**write_headers(csrf), "If-Match": f'"{txn["version"]}"'},
    )
    assert voided.status_code == 200
    reopened = next(i for i in _occurrences(browser) if i["id"] == occurrence["id"])
    assert (reopened["status"], reopened["transaction_id"]) == ("pending", None)
    assert _confirm(browser, csrf, reopened, "35100.00").status_code == 200
    assert _balance(browser, bank) == "35100.00"
    _journal_balanced(factory)


def test_editing_applies_forward_and_keeps_history(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    today = _today(factory)
    rule = _open_rule(browser, csrf, bank, today).json()
    current = _occurrences(browser)[0]
    _confirm(browser, csrf, current, "35000.00")
    changed = browser.patch(
        f"{API}/recurring/rules/{rule['id']}",
        json={"amount": {"amount": "37000.00", "currency": "TRY"}, "name": "Salary (raise)"},
        headers={**write_headers(csrf), "If-Match": f'"{rule["version"]}"'},
    )
    assert changed.status_code == 200, changed.text
    items = _occurrences(browser)
    confirmed = next(i for i in items if i["id"] == current["id"])
    assert confirmed["expected"]["amount"] == "35000.00"  # history never changes
    future = [
        i for i in items if i["status"] == "pending" and i["window_start"] > today.isoformat()
    ]
    assert all(i["expected"]["amount"] == "37000.00" for i in future)
    stale = browser.patch(
        f"{API}/recurring/rules/{rule['id']}", json={"name": "x"},
        headers={**write_headers(csrf), "If-Match": f'"{rule["version"]}"'},
    )  # fmt: skip
    assert stale.status_code == 412


def test_pausing_and_resuming_never_invents_missed_windows(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    rule = _rule(
        browser, csrf, bank, frequency="weekly", window_start_day=None, window_end_day=None
    ).json()
    before = _occurrences(browser)
    paused = browser.post(
        f"{API}/recurring/rules/{rule['id']}/pause",
        headers={**write_headers(csrf), "If-Match": f'"{rule["version"]}"'},
    ).json()
    assert paused["status"] == "paused"
    today = _today(factory).isoformat()
    remaining = _occurrences(browser)
    assert all(i["window_start"] <= today for i in remaining)
    assert len(remaining) < len(before)
    resumed = browser.post(
        f"{API}/recurring/rules/{rule['id']}/resume",
        headers={**write_headers(csrf), "If-Match": f'"{paused["version"]}"'},
    ).json()
    assert resumed["status"] == "active"
    assert all(i["window_end"] >= today for i in _occurrences(browser) if i["status"] == "pending")


def test_a_variable_amount_and_installments_with_a_count(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    today = _today(factory)
    freelance = _open_rule(browser, csrf, bank, today, name="Freelance", amount=None,
                           category_id=_category(browser, "freelance_business")).json()  # fmt: skip
    assert freelance["amount"] is None and freelance["next"]["expected"] is None
    phone = _rule(
        browser, csrf, bank, kind="expense", name="Phone installments",
        amount={"amount": "3000.00", "currency": "TRY"}, category_id=_category(browser, "shopping"),
        start_date=today.replace(day=1).isoformat(), total_occurrences=10,
    ).json()  # fmt: skip
    assert phone["next"]["sequence"] == 1 and phone["next"]["total"] == 10


def test_a_rule_in_another_currency_needs_a_conversion_when_confirmed(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    _open_rule(
        browser, csrf, bank, _today(factory), name="Remote client", currency="USD",
        amount={"amount": "1000.00", "currency": "USD"},
        category_id=_category(browser, "freelance_business"),
    )  # fmt: skip
    occurrence = _occurrences(browser)[0]
    assert occurrence["expected"] == {"amount": "1000.00", "currency": "USD"}
    refused = _confirm(browser, csrf, occurrence, "1000.00")
    assert refused.json()["code"] == "conversion_required"
    done = _confirm(
        browser, csrf, occurrence, "1000.00",
        conversion={"method": "actual_amount", "account_amount": "49800.00"},
    )  # fmt: skip
    assert done.status_code == 200, done.text
    txn = done.json()["transaction"]
    assert txn["amount"] == {"amount": "49800.00", "currency": "TRY"}
    assert txn["original_amount"] == {"amount": "1000.00", "currency": "USD"}


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"window_start_day": 28, "window_end_day": 25}, "invalid_window"),
        ({"frequency": "yearly"}, "invalid_window"),
        ({"category_id": None}, "validation_error"),
        ({"kind": "expense"}, "category_kind_mismatch"),
        (
            {"currency": "QQQ", "amount": {"amount": "1.00", "currency": "QQQ"}},
            "unsupported_currency",
        ),
        ({"amount": {"amount": "-5", "currency": "TRY"}}, "amount_not_positive"),
        ({"amount": {"amount": "5.001", "currency": "TRY"}}, "invalid_amount"),
        ({"name": "  "}, "validation_error"),
        ({"total_occurrences": 0}, "validation_error"),
    ],
)
def test_invalid_rules_are_refused(
    browser: TestClient, google: FakeGoogle, over: dict[str, Any], code: str
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    response = _rule(browser, csrf, bank, **over)
    assert response.status_code in (409, 422), response.text
    assert response.json()["code"] == code


def test_another_user_cant_see_or_answer_my_occurrences(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    _open_rule(browser, csrf, bank, _today(factory))
    occurrence = _occurrences(browser)[0]
    with TestClient(auth_app) as other:
        other_csrf = sign_in(other, google, subject="g-other", email="other@example.com")
        _setup(other, other_csrf)
        assert _occurrences(other) == []
        assert _confirm(other, other_csrf, occurrence, "1.00").status_code == 404
        assert (
            other.post(
                f"{API}/recurring/occurrences/{occurrence['id']}/skip",
                headers=write_headers(other_csrf),
            ).status_code
            == 404
        )


# --- Debts


def _debt(client: TestClient, csrf: str, **body: Any) -> Any:
    return client.post(f"{API}/debts", json=body, headers=_keyed(csrf))


def _pay(
    client: TestClient,
    csrf: str,
    debt: dict[str, Any],
    account: dict[str, Any],
    amount: str,
    **extra: Any,
) -> Any:
    return client.post(
        f"{API}/debts/{debt['id']}/payments",
        json={
            "account_id": account["id"],
            "amount": {"amount": amount, "currency": debt["currency"]},
            **extra,
        },
        headers=_keyed(csrf),
    )


def test_borrowing_and_repaying_are_neither_income_nor_spending(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    today = _today(factory)
    created = _debt(
        browser, csrf, direction="i_owe", counterparty="Ahmet",
        amount={"amount": "10000.00", "currency": "TRY"}, account_id=bank["id"],
    )  # fmt: skip
    assert created.status_code == 201, created.text
    debt = created.json()
    assert (debt["remaining"]["amount"], debt["settled"]) == ("10000.00", False)
    assert _balance(browser, bank) == "10000.00"
    paid = _pay(browser, csrf, debt, bank, "3000.00")
    assert paid.status_code == 201, paid.text
    assert paid.json()["debt"]["remaining"]["amount"] == "7000.00"
    assert paid.json()["debt"]["repaid"]["amount"] == "3000.00"
    assert _balance(browser, bank) == "7000.00"
    assert _month_totals(browser, today) == []  # no income, no spending
    too_much = _pay(browser, csrf, debt, bank, "7000.01")
    assert too_much.json()["code"] == "exceeds_remaining"
    assert too_much.json()["params"]["remaining"] == "7000.00"
    assert _pay(browser, csrf, debt, bank, "7000.00").status_code == 201
    settled = browser.get(f"{API}/debts/{debt['id']}").json()
    assert (settled["remaining"]["amount"], settled["settled"]) == ("0.00", True)
    assert [h["type"] for h in settled["history"]] == ["debt_payment", "debt_payment", "debt_start"]
    assert _pay(browser, csrf, debt, bank, "1.00").json()["code"] == "debt_settled"
    _journal_balanced(factory)


def test_lending_and_a_debt_from_before_finpilot(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    accounts = _setup(browser, csrf)
    cash = accounts["cash"]
    lent = _debt(
        browser, csrf, direction="owed_to_me", counterparty="Mina",
        amount={"amount": "2000.00", "currency": "TRY"}, account_id=cash["id"],
    ).json()  # fmt: skip
    assert _balance(browser, cash) == "-2000.00"
    back = _pay(browser, csrf, lent, cash, "500.00").json()
    assert back["debt"]["remaining"]["amount"] == "1500.00"
    assert _balance(browser, cash) == "-1500.00"
    older = _debt(
        browser, csrf, direction="i_owe", counterparty="Bank loan",
        amount={"amount": "50000.00", "currency": "TRY"},
    ).json()  # fmt: skip
    assert older["remaining"]["amount"] == "50000.00"
    assert _balance(browser, cash) == "-1500.00"  # no money moved
    assert _month_totals(browser, _today(factory)) == []
    _journal_balanced(factory)


def test_a_repayment_from_an_account_in_another_currency_keeps_both_amounts(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    debt = _debt(
        browser, csrf, direction="i_owe", counterparty="Lena",
        amount={"amount": "500.00", "currency": "EUR"},
    ).json()  # fmt: skip
    needs = _pay(browser, csrf, debt, bank, "100.00")
    assert needs.json()["code"] == "account_amount_required"
    paid = _pay(browser, csrf, debt, bank, "100.00", account_amount="5630.00").json()
    assert paid["debt"]["remaining"] == {"amount": "400.00", "currency": "EUR"}
    assert paid["transaction"]["amount"] == {"amount": "5630.00", "currency": "TRY"}
    assert paid["transaction"]["debt_amount"] == {"amount": "100.00", "currency": "EUR"}
    assert _balance(browser, bank) == "-5630.00"
    _journal_balanced(factory)


def test_a_debts_start_cant_be_voided_while_repayments_stand(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    debt = _debt(
        browser, csrf, direction="i_owe", counterparty="Ahmet",
        amount={"amount": "1000.00", "currency": "TRY"}, account_id=bank["id"],
    ).json()  # fmt: skip
    payment = _pay(browser, csrf, debt, bank, "100.00").json()["transaction"]
    start = browser.get(f"{API}/debts/{debt['id']}").json()["history"][-1]
    refused = browser.post(
        f"{API}/transactions/{start['id']}/void",
        headers={**write_headers(csrf), "If-Match": f'"{start["version"]}"'},
    )
    assert refused.json()["code"] == "debt_has_payments"
    browser.post(
        f"{API}/transactions/{payment['id']}/void",
        headers={**write_headers(csrf), "If-Match": f'"{payment["version"]}"'},
    )
    assert browser.get(f"{API}/debts/{debt['id']}").json()["remaining"]["amount"] == "1000.00"
    assert (
        browser.post(
            f"{API}/transactions/{start['id']}/void",
            headers={**write_headers(csrf), "If-Match": f'"{start["version"]}"'},
        ).status_code
        == 200
    )
    assert browser.get(f"{API}/debts/{debt['id']}").json()["remaining"]["amount"] == "0.00"
    assert _balance(browser, bank) == "0.00"


def test_installments_linked_to_a_debt_post_repayments(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    today = _today(factory)
    debt = _debt(
        browser, csrf, direction="i_owe", counterparty="Car loan",
        amount={"amount": "30000.00", "currency": "TRY"},
    ).json()  # fmt: skip
    rule = _rule(
        browser, csrf, bank, kind="debt_payment", name="Car loan", category_id=None,
        debt_id=debt["id"], amount={"amount": "3000.00", "currency": "TRY"},
        start_date=today.replace(day=1).isoformat(), total_occurrences=10,
    )  # fmt: skip
    assert rule.status_code == 201, rule.text
    occurrence = _occurrences(browser)[0]
    done = _confirm(browser, csrf, occurrence, "3000.00")
    assert done.status_code == 200, done.text
    assert done.json()["transaction"]["type"] == "debt_payment"
    assert browser.get(f"{API}/debts/{debt['id']}").json()["remaining"]["amount"] == "27000.00"
    assert _month_totals(browser, today) == []


def test_archiving_a_debt_needs_it_settled(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    debt = _debt(
        browser, csrf, direction="owed_to_me", counterparty="Mina",
        amount={"amount": "100.00", "currency": "TRY"}, account_id=bank["id"],
    ).json()  # fmt: skip
    headers = {**write_headers(csrf), "If-Match": f'"{debt["version"]}"'}
    assert (
        browser.post(f"{API}/debts/{debt['id']}/archive", headers=headers).json()["code"]
        == "debt_not_settled"
    )
    _pay(browser, csrf, debt, bank, "100.00")
    archived = browser.post(f"{API}/debts/{debt['id']}/archive", headers=headers)
    assert archived.json()["status"] == "archived"


def test_another_user_cant_touch_my_debts(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    debt = _debt(
        browser, csrf, direction="i_owe", counterparty="Ahmet",
        amount={"amount": "100.00", "currency": "TRY"}, account_id=bank["id"],
    ).json()  # fmt: skip
    with TestClient(auth_app) as other:
        other_csrf = sign_in(other, google, subject="g-other", email="other@example.com")
        other_bank = _setup(other, other_csrf)["bank"]
        assert other.get(f"{API}/debts").json() == []
        assert other.get(f"{API}/debts/{debt['id']}").status_code == 404
        assert _pay(other, other_csrf, debt, other_bank, "1.00").status_code == 404


def test_account_activity_signs_debt_movements_from_the_accounts_side(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    bank = _setup(browser, csrf)["bank"]
    debt = _debt(
        browser, csrf, direction="i_owe", counterparty="Ahmet",
        amount={"amount": "1000.00", "currency": "TRY"}, account_id=bank["id"],
    ).json()  # fmt: skip
    _pay(browser, csrf, debt, bank, "400.00")
    items = browser.get(f"{API}/accounts/{bank['id']}/activity").json()["items"]
    assert [(i["type"], i["amount"]["amount"]) for i in items] == [
        ("debt_payment", "-400.00"),
        ("debt_start", "1000.00"),
    ]
