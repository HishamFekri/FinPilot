"""Financial accounts through the real routes (API_SPEC.md §4, FINANCIAL_RULES.md §4).

Users sign in through the real Google routes against a fake Google (MOCKED, tests only).
"""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import FinancialAccount, IdempotencyKey, JournalEntry, JournalLine, Transaction
from tests.auth_harness import actions, count, sign_in, write_headers
from tests.google_fakes import FakeGoogle

pytestmark = pytest.mark.db

ACCOUNTS = "/api/v1/accounts"


def _post(browser: TestClient, csrf: str, body: dict[str, Any], key: str | None = None) -> Any:
    headers = write_headers(csrf)
    headers["Idempotency-Key"] = key or str(uuid.uuid4())
    return browser.post(ACCOUNTS, json=body, headers=headers)


def _create(browser: TestClient, csrf: str, **fields: Any) -> dict[str, Any]:
    body = {"name": "Cash", "type": "cash", "currency": "TRY", **fields}
    response = _post(browser, csrf, body)
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- Creating and reading


def test_an_account_starts_at_zero_with_no_journal_entry(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    account = _create(browser, csrf, name="Wallet", currency="EUR")
    assert account["balance"] == {"amount": "0.00", "currency": "EUR"}
    assert account["status"] == "active"
    assert count(factory, JournalEntry) == 0
    assert "account.created" in actions(factory)


def test_an_opening_balance_is_a_real_balanced_posting(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    account = _create(
        browser,
        csrf,
        name="Ziraat Checking",
        type="bank",
        opened_on="2026-01-01",
        opening_balance={"amount": "12450.75", "currency": "TRY"},
    )
    assert account["balance"] == {"amount": "12450.75", "currency": "TRY"}
    assert browser.get(f"{ACCOUNTS}/{account['id']}").json()["balance"]["amount"] == "12450.75"

    with factory() as db:
        opening = db.scalars(select(Transaction)).one()
        assert (opening.type, opening.amount_minor, opening.direction) == (
            "opening_balance",
            1245075,
            "increase",
        )
        lines = db.scalars(select(JournalLine.amount_minor).order_by(JournalLine.amount_minor))
        assert list(lines) == [-1245075, 1245075]  # asset debit, Equity:Opening credit


def test_a_negative_opening_balance_is_allowed(browser: TestClient, google: FakeGoogle) -> None:
    """A manually tracked account can be overdrawn (Assumption #5)."""
    csrf = sign_in(browser, google)
    account = _create(browser, csrf, opening_balance={"amount": "-80.10", "currency": "TRY"})
    assert account["balance"]["amount"] == "-80.10"


@pytest.mark.parametrize(
    ("currency", "amount", "shown"),
    [("JPY", "1500", "1500"), ("KWD", "12.345", "12.345"), ("USD", "0.01", "0.01")],
)
def test_amounts_follow_each_currencys_decimals(
    browser: TestClient, google: FakeGoogle, currency: str, amount: str, shown: str
) -> None:
    csrf = sign_in(browser, google)
    account = _create(
        browser, csrf, currency=currency, opening_balance={"amount": amount, "currency": currency}
    )
    assert account["balance"] == {"amount": shown, "currency": currency}


@pytest.mark.parametrize(
    ("opening", "code"),
    [
        ({"amount": "12.345", "currency": "TRY"}, "invalid_amount"),  # too many decimals
        ({"amount": "1,250.00", "currency": "TRY"}, "invalid_amount"),
        ({"amount": "1e3", "currency": "TRY"}, "invalid_amount"),
        ({"amount": 12.5, "currency": "TRY"}, "validation_error"),  # JSON numbers are refused (M2)
        ({"amount": "10.00", "currency": "EUR"}, "currency_mismatch"),
        ({"amount": "10000000000000.01", "currency": "TRY"}, "invalid_amount"),
    ],
)
def test_bad_opening_balances_are_refused_and_nothing_is_written(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    opening: dict[str, Any],
    code: str,
) -> None:
    csrf = sign_in(browser, google)
    response = _post(
        browser,
        csrf,
        {"name": "Cash", "type": "cash", "currency": "TRY", "opening_balance": opening},
    )
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert count(factory, FinancialAccount) == 0
    assert count(factory, IdempotencyKey) == 0  # rolled back with the failure, so a retry runs


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"name": "Cash", "type": "cash", "currency": "XXX"}, "unsupported_currency"),
        ({"name": "Cash", "type": "cash", "currency": "BGN"}, "unsupported_currency"),  # retired
        ({"name": "Cash", "type": "cash", "currency": "try"}, "validation_error"),
        ({"name": "Cash", "type": "credit", "currency": "TRY"}, "validation_error"),
        ({"name": "  ", "type": "cash", "currency": "TRY"}, "validation_error"),
        ({"name": "Ca\u202esh", "type": "cash", "currency": "TRY"}, "validation_error"),
        ({"name": "x" * 101, "type": "cash", "currency": "TRY"}, "validation_error"),
        ({"name": "Cash", "type": "cash", "currency": "TRY", "balance": "5"}, "validation_error"),
        (
            {"name": "Cash", "type": "cash", "currency": "TRY", "user_id": str(uuid.uuid4())},
            "validation_error",
        ),
        (
            {"name": "Cash", "type": "cash", "currency": "TRY", "opened_on": "2999-01-01"},
            "date_in_future",
        ),
    ],
)
def test_invalid_accounts_are_refused(
    browser: TestClient, google: FakeGoogle, body: dict[str, Any], code: str
) -> None:
    csrf = sign_in(browser, google)
    response = _post(browser, csrf, body)
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_active_names_are_unique_per_user(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    _create(browser, csrf, name="Cash")
    response = _post(browser, csrf, {"name": "CASH", "type": "cash", "currency": "EUR"})
    assert response.status_code == 409
    assert response.json()["code"] == "account_name_taken"


def test_names_are_stored_trimmed(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    assert _create(browser, csrf, name="  Kasa  ")["name"] == "Kasa"


# --- Idempotency (FINANCIAL_RULES.md §14.1, INV-08)


def test_a_repeated_request_is_replayed_not_repeated(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    key = str(uuid.uuid4())
    body = {
        "name": "Cash",
        "type": "cash",
        "currency": "TRY",
        "opening_balance": {"amount": "100.00", "currency": "TRY"},
    }
    first = _post(browser, csrf, body, key)
    again = _post(browser, csrf, body, key)
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert count(factory, FinancialAccount) == 1
    assert count(factory, JournalEntry) == 1


def test_a_reused_key_with_a_different_request_is_refused(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    key = str(uuid.uuid4())
    assert (
        _post(browser, csrf, {"name": "Cash", "type": "cash", "currency": "TRY"}, key).status_code
        == 201
    )
    other = _post(browser, csrf, {"name": "Bank", "type": "bank", "currency": "TRY"}, key)
    assert other.status_code == 422
    assert other.json()["code"] == "idempotency_key_reused"
    assert count(factory, FinancialAccount) == 1


@pytest.mark.parametrize(
    ("key", "code"), [(None, "idempotency_key_required"), ("abc", "invalid_idempotency_key")]
)
def test_creating_needs_a_valid_idempotency_key(
    browser: TestClient, google: FakeGoogle, key: str | None, code: str
) -> None:
    csrf = sign_in(browser, google)
    headers = write_headers(csrf)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = browser.post(
        ACCOUNTS, json={"name": "Cash", "type": "cash", "currency": "TRY"}, headers=headers
    )
    assert response.status_code == 400
    assert response.json()["code"] == code


def test_concurrent_requests_with_one_key_create_one_account(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """Double-click or retry while the first request is still running: still one account."""
    csrf = sign_in(browser, google)
    key = str(uuid.uuid4())
    body = {
        "name": "Cash",
        "type": "cash",
        "currency": "TRY",
        "opening_balance": {"amount": "5.00", "currency": "TRY"},
    }
    statuses: list[int] = []
    barrier = threading.Barrier(4)

    def send() -> None:
        barrier.wait()
        statuses.append(_post(browser, csrf, body, key).status_code)

    threads = [threading.Thread(target=send) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses == [201, 201, 201, 201]
    assert count(factory, FinancialAccount) == 1
    assert count(factory, JournalEntry) == 1


# --- Ownership and protection


def test_another_users_accounts_are_invisible(
    browser: TestClient, google: FakeGoogle, auth_app: Any
) -> None:
    owner_csrf = sign_in(browser, google, sub="owner-sub", email="owner@example.com")
    theirs = _create(browser, owner_csrf, opening_balance={"amount": "10.00", "currency": "TRY"})

    with TestClient(auth_app, raise_server_exceptions=False) as other:
        csrf = sign_in(other, google, sub="other-sub", email="other@example.com")
        path = f"{ACCOUNTS}/{theirs['id']}"
        headers = write_headers(csrf)
        assert other.get(ACCOUNTS).json() == []
        assert other.get(path).status_code == 404
        assert (
            other.patch(
                path, json={"name": "Mine"}, headers={**headers, "If-Match": '"1"'}
            ).status_code
            == 404
        )
        assert other.post(f"{path}/archive", headers=headers).status_code == 404
        assert other.post(f"{path}/unarchive", headers=headers).status_code == 404
        assert other.delete(path, headers=headers).status_code == 404

    # Untouched.
    assert browser.get(f"{ACCOUNTS}/{theirs['id']}").json()["name"] == "Cash"


def test_signed_out_requests_are_refused(browser: TestClient) -> None:
    assert browser.get(ACCOUNTS).status_code == 401
    assert browser.post(ACCOUNTS, json={}).status_code == 401
    assert browser.get("/api/v1/currencies").status_code == 401


def test_changes_need_csrf(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    body = {"name": "Cash", "type": "cash", "currency": "TRY"}
    no_token = browser.post(
        ACCOUNTS, json=body, headers={**write_headers(None), "Idempotency-Key": str(uuid.uuid4())}
    )
    other_site = browser.post(
        ACCOUNTS,
        json=body,
        headers={
            **write_headers(csrf, "https://evil.example"),
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert no_token.status_code == other_site.status_code == 403
    assert browser.get(ACCOUNTS).json() == []


def test_account_responses_are_never_cached(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    created = _post(browser, csrf, {"name": "Cash", "type": "cash", "currency": "TRY"})
    for response in (
        created,
        browser.get(ACCOUNTS),
        browser.get(f"{ACCOUNTS}/{created.json()['id']}"),
    ):
        assert response.headers["Cache-Control"] == "no-store"


# --- Changing, archiving, deleting


def test_renaming_needs_the_current_version(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    account = _create(browser, csrf)
    path = f"{ACCOUNTS}/{account['id']}"
    headers = write_headers(csrf)

    assert browser.patch(path, json={"name": "Kasa"}, headers=headers).status_code == 428
    renamed = browser.patch(path, json={"name": "Kasa"}, headers={**headers, "If-Match": '"1"'})
    assert renamed.status_code == 200
    assert (renamed.json()["name"], renamed.json()["version"]) == ("Kasa", 2)
    assert renamed.headers["ETag"] == '"2"'

    stale = browser.patch(path, json={"name": "Old tab"}, headers={**headers, "If-Match": '"1"'})
    assert stale.status_code == 412
    assert stale.json()["code"] == "version_conflict"
    assert browser.get(path).json()["name"] == "Kasa"


@pytest.mark.parametrize("body", [{"currency": "EUR"}, {"type": "bank"}, {"name": None}, {}])
def test_currency_and_type_never_change(
    browser: TestClient, google: FakeGoogle, body: dict[str, Any]
) -> None:
    csrf = sign_in(browser, google)
    account = _create(browser, csrf)
    response = browser.patch(
        f"{ACCOUNTS}/{account['id']}", json=body, headers={**write_headers(csrf), "If-Match": '"1"'}
    )
    assert response.status_code == 422


def test_archiving_needs_a_zero_balance(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    funded = _create(browser, csrf, opening_balance={"amount": "1.00", "currency": "TRY"})
    empty = _create(browser, csrf, name="Empty")
    headers = write_headers(csrf)

    refused = browser.post(f"{ACCOUNTS}/{funded['id']}/archive", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["code"] == "balance_not_zero"

    archived = browser.post(f"{ACCOUNTS}/{empty['id']}/archive", headers=headers).json()
    assert archived["status"] == "archived"
    assert [a["name"] for a in browser.get(ACCOUNTS, params={"status": "active"}).json()] == [
        "Cash"
    ]
    assert [a["name"] for a in browser.get(ACCOUNTS, params={"status": "archived"}).json()] == [
        "Empty"
    ]
    restored = browser.post(f"{ACCOUNTS}/{empty['id']}/unarchive", headers=headers).json()
    assert restored["status"] == "active"


def test_only_an_account_without_entries_can_be_deleted(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    funded = _create(browser, csrf, opening_balance={"amount": "1.00", "currency": "TRY"})
    empty = _create(browser, csrf, name="Empty")
    headers = write_headers(csrf)

    refused = browser.delete(f"{ACCOUNTS}/{funded['id']}", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["code"] == "account_has_entries"
    assert browser.delete(f"{ACCOUNTS}/{empty['id']}", headers=headers).status_code == 204
    assert browser.get(f"{ACCOUNTS}/{empty['id']}").status_code == 404
    assert count(factory, FinancialAccount) == 1


def test_balances_never_mix_currencies(browser: TestClient, google: FakeGoogle) -> None:
    """Each account reports in its own currency; nothing adds TRY to EUR (§12 of the brief)."""
    csrf = sign_in(browser, google)
    _create(browser, csrf, name="TRY", opening_balance={"amount": "100.00", "currency": "TRY"})
    _create(
        browser,
        csrf,
        name="EUR",
        currency="EUR",
        opening_balance={"amount": "7.50", "currency": "EUR"},
    )
    balances = {a["name"]: a["balance"] for a in browser.get(ACCOUNTS).json()}
    assert balances == {
        "TRY": {"amount": "100.00", "currency": "TRY"},
        "EUR": {"amount": "7.50", "currency": "EUR"},
    }


def test_the_derived_balance_equals_the_sum_of_asset_lines(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """INV-04 in its simplest form, until transactions exist to vary it (A4)."""
    csrf = sign_in(browser, google)
    for index, amount in enumerate(["0.01", "999999.99", "-50.00"]):
        _create(
            browser, csrf, name=f"A{index}", opening_balance={"amount": amount, "currency": "TRY"}
        )
    with factory() as db:
        total = db.scalar(
            select(func.sum(JournalLine.amount_minor)).where(JournalLine.amount_minor != 0)
        )
    assert total == 0  # every entry balances
    shown = sorted(a["balance"]["amount"] for a in browser.get(ACCOUNTS).json())
    assert shown == ["-50.00", "0.01", "999999.99"]


def test_currencies_list_their_decimal_places(browser: TestClient, google: FakeGoogle) -> None:
    sign_in(browser, google)
    listed = {c["code"]: c["exponent"] for c in browser.get("/api/v1/currencies").json()}
    assert (listed["TRY"], listed["JPY"], listed["KWD"]) == (2, 0, 3)
    assert "BGN" not in listed
