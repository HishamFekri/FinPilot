"""A4: setup, categories, income and expense, history, corrections, and the monthly summary.

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
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Category,
    FinancialAccount,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Transaction,
    User,
)
from tests.auth_harness import count, sign_in, write_headers
from tests.google_fakes import FakeGoogle

pytestmark = pytest.mark.db

API = "/api/v1"
EXPENSE_DEFAULTS = 15  # 14 in A4, plus Interest & fees (0016)
INCOME_DEFAULTS = 5


def _keyed(csrf: str, key: str | None = None) -> dict[str, str]:
    return {**write_headers(csrf), "Idempotency-Key": key or str(uuid.uuid4())}


def _ready(client: TestClient, csrf: str, currency: str = "TRY") -> dict[str, Any]:
    """Runs setup with a currency and returns the default account."""
    response = client.post(f"{API}/setup", json={"currency": currency}, headers=write_headers(csrf))
    assert response.status_code == 200, response.text
    accounts = client.get(f"{API}/accounts").json()
    return next(a for a in accounts if a["is_default"])


def _category(client: TestClient, key: str) -> dict[str, Any]:
    return next(c for c in client.get(f"{API}/categories").json() if c["system_key"] == key)


def _record(
    client: TestClient,
    csrf: str,
    account: dict[str, Any],
    amount: str,
    category: str = "groceries",
    kind: str = "expense",
    key: str | None = None,
    **extra: Any,
) -> Any:
    body = {
        "type": kind,
        "account_id": account["id"],
        "amount": {"amount": amount, "currency": account["currency"]},
        "category_id": _category(client, category)["id"],
        **extra,
    }
    return client.post(f"{API}/transactions", json=body, headers=_keyed(csrf, key))


def _balance(client: TestClient, account: dict[str, Any]) -> str:
    return str(client.get(f"{API}/accounts/{account['id']}").json()["balance"]["amount"])


def _entries_balance(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        sums: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        for entry_id, currency, amount in db.execute(
            select(JournalLine.entry_id, JournalLine.currency, JournalLine.amount_minor)
        ):
            sums[(entry_id, currency)] += amount
    assert all(total == 0 for total in sums.values())


# --- Setup: default accounts and categories


def test_a_new_user_is_asked_only_for_a_currency(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    assert browser.get(f"{API}/setup").json() == {
        "completed": False,
        "needs_currency": True,
        "preferred_currency": None,
        "default_account_id": None,
    }
    refused = browser.post(f"{API}/setup", json={}, headers=write_headers(csrf))
    assert (refused.status_code, refused.json()["code"]) == (409, "currency_required")
    assert count(factory, FinancialAccount) == 0


def test_setup_creates_cash_and_bank_at_zero_and_seeds_categories(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf, "TRY")
    accounts = {a["system_key"]: a for a in browser.get(f"{API}/accounts").json()}
    assert set(accounts) == {"cash", "bank"}
    assert cash["system_key"] == "cash"
    assert all(a["name"] is None for a in accounts.values())  # shown translated by the apps
    assert all(a["balance"] == {"amount": "0.00", "currency": "TRY"} for a in accounts.values())
    assert count(factory, JournalEntry) == 0  # no invented opening entries
    listed = browser.get(f"{API}/categories").json()
    assert sum(c["kind"] == "expense" for c in listed) == EXPENSE_DEFAULTS
    assert sum(c["kind"] == "income" for c in listed) == INCOME_DEFAULTS
    assert browser.get("/api/v1/auth/me").json()["preferred_currency"] == "TRY"


def test_setup_runs_once_however_often_it_is_called(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    statuses: list[int] = []
    barrier = threading.Barrier(5)

    def call() -> None:
        barrier.wait()
        statuses.append(
            browser.post(
                f"{API}/setup", json={"currency": "EUR"}, headers=write_headers(csrf)
            ).status_code
        )

    threads = [threading.Thread(target=call) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses == [200] * 5
    browser.post(f"{API}/setup", json={"currency": "USD"}, headers=write_headers(csrf))
    assert count(factory, FinancialAccount) == 2
    assert count(factory, Category) == EXPENSE_DEFAULTS + INCOME_DEFAULTS
    # A later call with another currency changes nothing: setup already ran.
    assert {a["currency"] for a in browser.get(f"{API}/accounts").json()} == {"EUR"}


def test_a_user_with_accounts_keeps_them_and_gets_no_duplicates(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    mine = browser.post(
        f"{API}/accounts",
        json={"name": "Ziraat", "type": "bank", "currency": "TRY",
              "opening_balance": {"amount": "50.00", "currency": "TRY"}},
        headers=_keyed(csrf),
    ).json()  # fmt: skip
    state = browser.post(f"{API}/setup", json={}, headers=write_headers(csrf)).json()
    assert state["completed"] is True
    assert state["default_account_id"] == mine["id"]
    accounts = browser.get(f"{API}/accounts").json()
    assert [(a["name"], a["balance"]["amount"]) for a in accounts] == [("Ziraat", "50.00")]
    assert count(factory, Category) == EXPENSE_DEFAULTS + INCOME_DEFAULTS


def test_an_archived_default_falls_back_without_posting_to_it(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    bank = next(a for a in browser.get(f"{API}/accounts").json() if a["system_key"] == "bank")
    browser.post(f"{API}/accounts/{cash['id']}/archive", headers=write_headers(csrf))
    assert browser.get(f"{API}/setup").json()["default_account_id"] == bank["id"]
    refused = _record(browser, csrf, cash, "5.00")
    assert (refused.status_code, refused.json()["code"]) == (409, "account_archived")
    made = browser.post(f"{API}/accounts/{cash['id']}/make-default", headers=write_headers(csrf))
    assert (made.status_code, made.json()["code"]) == (409, "account_archived")
    browser.post(f"{API}/accounts/{cash['id']}/unarchive", headers=write_headers(csrf))
    browser.post(f"{API}/accounts/{bank['id']}/make-default", headers=write_headers(csrf))
    assert browser.get(f"{API}/setup").json()["default_account_id"] == bank["id"]


# --- Recording income and expenses


def test_an_expense_lowers_the_account_and_counts_as_spending(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    response = _record(browser, csrf, cash, "42.30", "groceries", description="Migros")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["type"], body["amount"]["amount"], body["category"]["system_key"]) == (
        "expense",
        "42.30",
        "groceries",
    )
    assert [c["action"] for c in body["changes"]] == ["created"]
    assert _balance(browser, cash) == "-42.30"
    with factory() as db:
        rows = db.execute(
            select(LedgerAccount.kind, JournalLine.amount_minor).join(
                LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id
            )
        ).all()
        lines = {str(kind): int(amount) for kind, amount in rows}
        entry_category = db.scalar(select(JournalEntry.category_id))
    assert lines == {"expense": 4230, "asset": -4230}
    assert str(entry_category) == body["category"]["id"]


def test_income_raises_the_account_and_counts_as_income(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    response = _record(browser, csrf, cash, "35000.00", "salary", kind="income")
    assert response.status_code == 201, response.text
    assert _balance(browser, cash) == "35000.00"
    month = browser.get(f"{API}/summary").json()["currencies"]
    assert month == [
        {
            "currency": "TRY",
            "expenses": {"amount": "0.00", "currency": "TRY"},
            "income": {"amount": "35000.00", "currency": "TRY"},
            "transactions": 1,
            "top_categories": [],
            "categories": [],
            "uncategorized": None,
            "trip_shares": None,
            "trip_paid": None,
            "trip_received": None,
        }
    ]


@pytest.mark.parametrize(
    ("change", "code", "status"),
    [
        ({"amount": {"amount": "1.005", "currency": "TRY"}}, "invalid_amount", 422),
        ({"amount": {"amount": "0", "currency": "TRY"}}, "amount_not_positive", 422),
        ({"amount": {"amount": "5.00", "currency": "EUR"}}, "conversion_required", 422),
        ({"amount": {"amount": 5, "currency": "TRY"}}, "validation_error", 422),
        ({"effective_date": "2999-01-01"}, "date_in_future", 422),
        ({"type": "income"}, "category_kind_mismatch", 422),
        ({"type": "transfer"}, "validation_error", 422),
    ],
)
def test_invalid_entries_change_nothing(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    change: dict[str, Any],
    code: str,
    status: int,
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    body = {
        "type": "expense",
        "account_id": cash["id"],
        "amount": {"amount": "5.00", "currency": "TRY"},
        "category_id": _category(browser, "groceries")["id"],
        **change,
    }
    response = browser.post(f"{API}/transactions", json=body, headers=_keyed(csrf))
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert count(factory, Transaction) == 0


def test_an_archived_category_cant_be_chosen_but_stays_on_history(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    gifts = _category(browser, "gifts")
    first = _record(browser, csrf, cash, "10.00", "gifts").json()
    archived = browser.patch(
        f"{API}/categories/{gifts['id']}",
        json={"status": "archived"},
        headers={**write_headers(csrf), "If-Match": f'"{gifts["version"]}"'},
    )
    assert archived.json()["status"] == "archived"
    assert "gifts" not in {c["system_key"] for c in browser.get(f"{API}/categories").json()}
    refused = browser.post(
        f"{API}/transactions",
        json={"type": "expense", "account_id": cash["id"],
              "amount": {"amount": "1.00", "currency": "TRY"}, "category_id": gifts["id"]},
        headers=_keyed(csrf),
    )  # fmt: skip
    assert (refused.status_code, refused.json()["code"]) == (409, "category_archived")
    kept = browser.get(f"{API}/transactions/{first['id']}").json()
    assert (kept["category"]["system_key"], kept["category"]["status"]) == ("gifts", "archived")


def test_a_repeated_submission_records_once(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    key = str(uuid.uuid4())
    statuses: list[int] = []
    barrier = threading.Barrier(4)

    def send() -> None:
        barrier.wait()
        statuses.append(_record(browser, csrf, cash, "12.00", key=key).status_code)

    threads = [threading.Thread(target=send) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses == [201] * 4
    assert count(factory, Transaction) == 1
    assert _balance(browser, cash) == "-12.00"


def test_concurrent_different_expenses_all_land(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    statuses: list[int] = []
    barrier = threading.Barrier(6)

    def send() -> None:
        barrier.wait()
        statuses.append(_record(browser, csrf, cash, "1.10").status_code)

    threads = [threading.Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses == [201] * 6
    assert _balance(browser, cash) == "-6.60"
    _entries_balance(factory)


def test_recording_needs_csrf_and_a_key(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    body = {
        "type": "expense",
        "account_id": cash["id"],
        "amount": {"amount": "1.00", "currency": "TRY"},
        "category_id": _category(browser, "groceries")["id"],
    }
    no_csrf = browser.post(
        f"{API}/transactions", json=body, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    no_key = browser.post(f"{API}/transactions", json=body, headers=write_headers(csrf))
    assert no_csrf.status_code == 403
    assert no_key.json()["code"] == "idempotency_key_required"


def test_another_users_records_are_invisible(
    browser: TestClient, google: FakeGoogle, auth_app: FastAPI
) -> None:
    csrf = sign_in(browser, google, sub="owner-sub", email="owner@example.com")
    cash = _ready(browser, csrf)
    mine = _record(browser, csrf, cash, "9.99").json()
    groceries = _category(browser, "groceries")

    with TestClient(auth_app, raise_server_exceptions=False) as other:
        other_csrf = sign_in(other, google, sub="other-sub", email="other@example.com")
        their_cash = _ready(other, other_csrf)
        assert other.get(f"{API}/transactions").json()["items"] == []
        assert other.get(f"{API}/transactions/{mine['id']}").status_code == 404
        headers = {**write_headers(other_csrf), "If-Match": '"1"'}
        assert (
            other.patch(
                f"{API}/transactions/{mine['id']}", json={"notes": "x"}, headers=headers
            ).status_code
            == 404
        )
        assert (
            other.post(f"{API}/transactions/{mine['id']}/void", headers=headers).status_code == 404
        )
        # Posting with someone else's category or account is refused.
        stolen = other.post(
            f"{API}/transactions",
            json={"type": "expense", "account_id": their_cash["id"],
                  "amount": {"amount": "1.00", "currency": "TRY"},
                  "category_id": groceries["id"]},
            headers=_keyed(other_csrf),
        )  # fmt: skip
        assert stolen.status_code == 404
        into_mine = other.post(
            f"{API}/transactions",
            json={"type": "expense", "account_id": cash["id"],
                  "amount": {"amount": "1.00", "currency": "TRY"},
                  "category_id": _category(other, "groceries")["id"]},
            headers=_keyed(other_csrf),
        )  # fmt: skip
        assert into_mine.status_code == 404
        assert other.get(f"{API}/summary").json()["currencies"] == []
    assert _balance(browser, cash) == "-9.99"


# --- Corrections and voids


def test_a_correction_reverses_and_replaces_without_editing_history(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    bank = next(a for a in browser.get(f"{API}/accounts").json() if a["system_key"] == "bank")
    created = _record(browser, csrf, cash, "100.00", "groceries").json()
    path = f"{API}/transactions/{created['id']}"

    fixed = browser.patch(
        path,
        json={
            "amount": {"amount": "80.00", "currency": "TRY"},
            "account_id": bank["id"],
            "category_id": _category(browser, "food_and_drinks")["id"],
            "description": "Dinner",
        },
        headers={**write_headers(csrf), "If-Match": '"1"'},
    )
    assert fixed.status_code == 200, fixed.text
    body = fixed.json()
    assert (body["amount"]["amount"], body["account"]["id"], body["version"]) == (
        "80.00",
        bank["id"],
        2,
    )
    assert [c["action"] for c in body["changes"]] == ["created", "corrected"]
    assert (_balance(browser, cash), _balance(browser, bank)) == ("0.00", "-80.00")
    assert count(factory, JournalEntry) == 3  # original, reversal, replacement
    top = browser.get(f"{API}/summary").json()["currencies"][0]
    assert top["expenses"]["amount"] == "80.00"
    assert [t["category"]["system_key"] for t in top["top_categories"]] == ["food_and_drinks"]

    stale = browser.patch(
        path, json={"notes": "x"}, headers={**write_headers(csrf), "If-Match": '"1"'}
    )
    assert (stale.status_code, stale.json()["code"]) == (412, "version_conflict")
    text_only = browser.patch(
        path, json={"notes": "With Ali"}, headers={**write_headers(csrf), "If-Match": '"2"'}
    )
    assert text_only.json()["notes"] == "With Ali"
    assert count(factory, JournalEntry) == 3  # a note carries no money
    _entries_balance(factory)


def test_a_correction_to_another_month_moves_it_there(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    created = _record(browser, csrf, cash, "30.00").json()
    last_month = date.fromisoformat(created["effective_date"]).replace(day=1) - timedelta(days=1)
    browser.patch(
        f"{API}/transactions/{created['id']}",
        json={"effective_date": last_month.isoformat()},
        headers={**write_headers(csrf), "If-Match": '"1"'},
    )
    this = browser.get(f"{API}/summary").json()["currencies"]
    before = browser.get(f"{API}/summary", params={"month": last_month.strftime("%Y-%m")}).json()
    assert this == [] or this[0]["expenses"]["amount"] == "0.00"
    assert before["currencies"][0]["expenses"]["amount"] == "30.00"


def test_a_void_cancels_the_effect_and_keeps_the_record(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    created = _record(browser, csrf, cash, "15.00").json()
    path = f"{API}/transactions/{created['id']}"
    headers = {**write_headers(csrf), "If-Match": '"1"'}
    voided = browser.post(f"{path}/void", headers=headers).json()
    assert voided["status"] == "reversed"
    assert [c["action"] for c in voided["changes"]] == ["created", "voided"]
    assert _balance(browser, cash) == "0.00"
    assert browser.get(f"{API}/summary").json()["currencies"] == []
    assert browser.get(f"{API}/transactions").json()["items"] == []
    shown = browser.get(f"{API}/transactions", params={"include_voided": "true"}).json()
    assert [i["status"] for i in shown["items"]] == ["reversed"]
    again = browser.post(f"{path}/void", headers={**write_headers(csrf), "If-Match": '"2"'})
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")
    edit = browser.patch(
        path, json={"notes": "x"}, headers={**write_headers(csrf), "If-Match": '"2"'}
    )
    assert edit.status_code == 409
    _entries_balance(factory)


# --- The monthly summary never mixes kinds or currencies


def test_the_summary_counts_only_income_and_expenses_per_currency(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    bank = next(a for a in browser.get(f"{API}/accounts").json() if a["system_key"] == "bank")
    euro = browser.post(
        f"{API}/accounts",
        json={"name": "Euro", "type": "savings", "currency": "EUR",
              "opening_balance": {"amount": "500.00", "currency": "EUR"}},
        headers=_keyed(csrf),
    ).json()  # fmt: skip
    _record(browser, csrf, cash, "100.00", "groceries")
    _record(browser, csrf, cash, "40.00", "food_and_drinks")
    _record(browser, csrf, cash, "60.00", "groceries")
    _record(browser, csrf, bank, "35000.00", "salary", kind="income")
    _record(browser, csrf, euro, "9.50", "travel")
    # Neither of these is spending or income.
    browser.post(
        f"{API}/transfers",
        json={"from_account_id": bank["id"], "to_account_id": cash["id"],
              "amount": {"amount": "500.00", "currency": "TRY"}},
        headers=_keyed(csrf),
    )  # fmt: skip
    browser.post(
        f"{API}/accounts/{cash['id']}/adjustments",
        json={"target_balance": {"amount": "250.00", "currency": "TRY"},
              "expected_balance": {"amount": "300.00", "currency": "TRY"}},
        headers=_keyed(csrf),
    )  # fmt: skip
    months = {c["currency"]: c for c in browser.get(f"{API}/summary").json()["currencies"]}
    assert months["TRY"]["expenses"]["amount"] == "200.00"
    assert months["TRY"]["income"]["amount"] == "35000.00"
    assert months["TRY"]["transactions"] == 4
    assert [
        (t["category"]["system_key"], t["amount"]["amount"])
        for t in months["TRY"]["top_categories"]
    ] == [
        ("groceries", "160.00"),
        ("food_and_drinks", "40.00"),
    ]
    assert months["EUR"]["expenses"] == {"amount": "9.50", "currency": "EUR"}
    assert months["EUR"]["income"]["amount"] == "0.00"  # the EUR opening balance isn't income


# --- History


def test_history_filters_searches_and_pages(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    cash = _ready(browser, csrf)
    bank = next(a for a in browser.get(f"{API}/accounts").json() if a["system_key"] == "bank")
    for index in range(7):
        _record(browser, csrf, cash, f"{index + 1}.00", "groceries", description=f"Market {index}")
    _record(browser, csrf, bank, "50.00", "health", description="Pharmacy 100%")
    _record(browser, csrf, bank, "1000.00", "salary", kind="income")

    def items(**params: Any) -> list[dict[str, Any]]:
        return list(browser.get(f"{API}/transactions", params=params).json()["items"])

    assert len(items()) == 9
    assert {i["type"] for i in items(type="income")} == {"income"}
    assert len(items(account_id=bank["id"])) == 2
    assert [i["description"] for i in items(category_id=_category(browser, "health")["id"])] == [
        "Pharmacy 100%"
    ]
    assert [i["description"] for i in items(q="100%")] == ["Pharmacy 100%"]  # literal %
    assert len(items(q="market")) == 7
    today = date.fromisoformat(items()[0]["effective_date"])
    assert items(**{"from": (today + timedelta(days=1)).isoformat()}) == []

    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 4}
        if cursor:
            params["cursor"] = cursor
        page = browser.get(f"{API}/transactions", params=params).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 9
    assert browser.get(f"{API}/transactions", params={"cursor": "%%%"}).status_code == 400


# --- Categories


def test_categories_can_be_added_renamed_and_reset(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    _ready(browser, csrf)
    created = browser.post(
        f"{API}/categories",
        json={"kind": "expense", "name": "Coffee", "icon": "coffee", "color": "amber"},
        headers=write_headers(csrf),
    )
    assert created.status_code == 201, created.text
    duplicate = browser.post(
        f"{API}/categories",
        json={"kind": "expense", "name": "coffee", "icon": "coffee", "color": "amber"},
        headers=write_headers(csrf),
    )
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "category_name_taken")
    bad_icon = browser.post(
        f"{API}/categories",
        json={"kind": "expense", "name": "Kids", "icon": "skull", "color": "amber"},
        headers=write_headers(csrf),
    )
    assert bad_icon.json()["code"] == "invalid_icon"

    rent = _category(browser, "housing")
    renamed = browser.patch(
        f"{API}/categories/{rent['id']}",
        json={"name": "Kira"},
        headers={**write_headers(csrf), "If-Match": f'"{rent["version"]}"'},
    ).json()
    assert (renamed["name"], renamed["system_key"]) == ("Kira", "housing")
    reset = browser.patch(
        f"{API}/categories/{rent['id']}",
        json={"name": None},
        headers={**write_headers(csrf), "If-Match": f'"{renamed["version"]}"'},
    ).json()
    assert reset["name"] is None
    custom_unnamed = browser.patch(
        f"{API}/categories/{created.json()['id']}",
        json={"name": None},
        headers={**write_headers(csrf), "If-Match": '"1"'},
    )
    assert custom_unnamed.status_code == 422
    with factory() as db:
        assert db.scalar(select(User.setup_completed_at)) is not None
