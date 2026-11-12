"""Balance adjustments, transfers, totals, and account activity, through the real routes.

FINANCIAL_RULES.md §4.4, §4.5, §14. Users sign in against a fake Google (MOCKED, tests only).
"""

import threading
import uuid
from collections import defaultdict
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import JournalEntry, JournalLine, LedgerAccount, Transaction
from tests.auth_harness import actions, count, sign_in, write_headers
from tests.google_fakes import FakeGoogle

pytestmark = pytest.mark.db

ACCOUNTS = "/api/v1/accounts"
TRANSFERS = "/api/v1/transfers"


def _key_headers(csrf: str, key: str | None = None) -> dict[str, str]:
    return {**write_headers(csrf), "Idempotency-Key": key or str(uuid.uuid4())}


def _account(
    client: TestClient, csrf: str, name: str, currency: str = "TRY", opening: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "type": "bank", "currency": currency}
    if opening is not None:
        body["opening_balance"] = {"amount": opening, "currency": currency}
    response = client.post(ACCOUNTS, json=body, headers=_key_headers(csrf))
    assert response.status_code == 201, response.text
    return dict(response.json())


def _balance(client: TestClient, account: dict[str, Any]) -> str:
    return str(client.get(f"{ACCOUNTS}/{account['id']}").json()["balance"]["amount"])


def _adjust(
    client: TestClient,
    csrf: str,
    account: dict[str, Any],
    target: str,
    expected: str,
    key: str | None = None,
    reason: str | None = None,
) -> Any:
    currency = account["currency"]
    body: dict[str, Any] = {
        "target_balance": {"amount": target, "currency": currency},
        "expected_balance": {"amount": expected, "currency": currency},
    }
    if reason is not None:
        body["reason"] = reason
    return client.post(
        f"{ACCOUNTS}/{account['id']}/adjustments", json=body, headers=_key_headers(csrf, key)
    )


def _transfer(
    client: TestClient,
    csrf: str,
    source: dict[str, Any],
    destination: dict[str, Any],
    amount: str,
    received: str | None = None,
    key: str | None = None,
    **extra: Any,
) -> Any:
    body: dict[str, Any] = {
        "from_account_id": source["id"],
        "to_account_id": destination["id"],
        "amount": {"amount": amount, "currency": source["currency"]},
        **extra,
    }
    if received is not None:
        body["received_amount"] = {"amount": received, "currency": destination["currency"]}
    return client.post(TRANSFERS, json=body, headers=_key_headers(csrf, key))


def _entries_balance(factory: sessionmaker[Session]) -> None:
    """Every entry sums to zero per currency (INV-01), read back from the database."""
    with factory() as db:
        sums: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        for entry_id, currency, amount in db.execute(
            select(JournalLine.entry_id, JournalLine.currency, JournalLine.amount_minor)
        ):
            sums[(entry_id, currency)] += amount
    assert all(total == 0 for total in sums.values())


# --- Adjustments


def test_the_backend_works_out_the_adjustment(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    wallet = _account(browser, csrf, "Wallet", opening="55.00")
    preview = browser.get(
        f"{ACCOUNTS}/{wallet['id']}/adjustment-preview",
        params={"target": "40.00", "currency": "TRY"},
    ).json()
    assert preview == {
        "current": {"amount": "55.00", "currency": "TRY"},
        "target": {"amount": "40.00", "currency": "TRY"},
        "difference": {"amount": "15.00", "currency": "TRY"},
        "direction": "decrease",
    }


def test_an_adjustment_posts_the_difference_and_keeps_history(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    wallet = _account(browser, csrf, "Wallet", opening="55.00")

    down = _adjust(browser, csrf, wallet, "40.00", "55.00", reason="Counted the cash")
    assert down.status_code == 201, down.text
    assert down.json()["difference"] == {"amount": "15.00", "currency": "TRY"}
    assert down.json()["direction"] == "decrease"
    assert down.json()["account"]["balance"]["amount"] == "40.00"

    up = _adjust(browser, csrf, wallet, "100.25", "40.00")
    assert up.status_code == 201
    assert up.json()["direction"] == "increase"
    assert _balance(browser, wallet) == "100.25"

    with factory() as db:
        kinds = set(
            db.scalars(
                select(LedgerAccount.kind)
                .join(JournalLine, JournalLine.ledger_account_id == LedgerAccount.id)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalEntry.transaction_type == "adjustment")
            )
        )
        reasons = list(
            db.scalars(select(Transaction.notes).where(Transaction.type == "adjustment"))
        )
    # Never income or spending: the counterparty is Equity:Adjustment (§4.4).
    assert kinds == {"asset", "equity_adjustment"}
    assert set(reasons) == {"Counted the cash", None}  # the reason is optional
    assert count(factory, JournalEntry) == 3  # opening + two adjustments; nothing rewritten
    assert actions(factory).count("account.adjusted") == 2
    _entries_balance(factory)


def test_an_adjustment_from_a_stale_balance_is_refused(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """The person agreed to a difference worked out from what they saw. If something was recorded
    since, that difference is no longer right."""
    csrf = sign_in(browser, google)
    wallet = _account(browser, csrf, "Wallet", opening="55.00")
    stale = _adjust(browser, csrf, wallet, "40.00", "50.00")
    assert stale.status_code == 409
    assert stale.json()["code"] == "balance_changed"
    assert _balance(browser, wallet) == "55.00"


@pytest.mark.parametrize(
    ("target", "expected", "currency", "code", "status"),
    [
        ("55.00", "55.00", "TRY", "no_change", 422),
        ("40.001", "55.00", "TRY", "invalid_amount", 422),
        ("40.00", "55.00", "EUR", "currency_mismatch", 422),
    ],
)
def test_invalid_adjustments_change_nothing(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    target: str,
    expected: str,
    currency: str,
    code: str,
    status: int,
) -> None:
    csrf = sign_in(browser, google)
    wallet = _account(browser, csrf, "Wallet", opening="55.00")
    response = browser.post(
        f"{ACCOUNTS}/{wallet['id']}/adjustments",
        json={
            "target_balance": {"amount": target, "currency": currency},
            "expected_balance": {"amount": expected, "currency": currency},
        },
        headers=_key_headers(csrf),
    )
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert count(factory, JournalEntry) == 1


def test_concurrent_adjustments_from_one_balance_post_once(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """Two tabs adjust the same 55.00 at once: one wins, the other is told the balance moved."""
    csrf = sign_in(browser, google)
    wallet = _account(browser, csrf, "Wallet", opening="55.00")
    statuses: list[int] = []
    barrier = threading.Barrier(2)

    def send(target: str) -> None:
        barrier.wait()
        statuses.append(_adjust(browser, csrf, wallet, target, "55.00").status_code)

    threads = [threading.Thread(target=send, args=(t,)) for t in ("40.00", "70.00")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [201, 409]
    assert _balance(browser, wallet) in {"40.00", "70.00"}
    assert count(factory, JournalEntry) == 2


def test_a_repeated_adjustment_is_replayed(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    wallet = _account(browser, csrf, "Wallet", opening="55.00")
    key = str(uuid.uuid4())
    first = _adjust(browser, csrf, wallet, "40.00", "55.00", key)
    again = _adjust(browser, csrf, wallet, "40.00", "55.00", key)
    assert first.status_code == again.status_code == 201
    assert again.headers["Idempotent-Replayed"] == "true"
    assert count(factory, JournalEntry) == 2


# --- Transfers


def test_a_same_currency_transfer_moves_money_and_preserves_the_total(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="1000.00")
    cash = _account(browser, csrf, "Cash", opening="20.00")

    response = _transfer(browser, csrf, bank, cash, "250.50", description="ATM")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transfer"]["cross_currency"] is False
    assert body["transfer"]["implied_rate"] is None
    assert body["from_account"]["balance"]["amount"] == "749.50"
    assert body["to_account"]["balance"]["amount"] == "270.50"
    total = browser.get(f"{ACCOUNTS}/totals").json()
    assert total == [{"balance": {"amount": "1020.00", "currency": "TRY"}, "accounts": 2}]

    with factory() as db:
        kinds = set(
            db.scalars(
                select(LedgerAccount.kind)
                .join(JournalLine, JournalLine.ledger_account_id == LedgerAccount.id)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalEntry.transaction_type == "transfer")
            )
        )
    assert kinds == {"asset"}  # neither income nor spending (INV-07)
    _entries_balance(factory)


def test_a_cross_currency_transfer_records_both_real_amounts(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """3,500.00 TRY out, 100.00 EUR in (FINANCIAL_RULES.md §4.5)."""
    csrf = sign_in(browser, google)
    lira = _account(browser, csrf, "Lira", opening="5000.00")
    euro = _account(browser, csrf, "Euro", currency="EUR")

    missing = _transfer(browser, csrf, lira, euro, "3500.00")
    assert missing.status_code == 422
    assert missing.json()["code"] == "received_amount_required"

    preview = browser.get(
        f"{TRANSFERS}/preview",
        params={
            "from_account_id": lira["id"],
            "to_account_id": euro["id"],
            "amount": "3500.00",
            "currency": "TRY",
            "received_amount": "100.00",
            "received_currency": "EUR",
        },
    ).json()
    assert preview["implied_rate"] == "0.028571"  # 100 / 3500, shown, never applied

    done = _transfer(browser, csrf, lira, euro, "3500.00", "100.00")
    assert done.status_code == 201, done.text
    assert done.json()["from_account"]["balance"] == {"amount": "1500.00", "currency": "TRY"}
    assert done.json()["to_account"]["balance"] == {"amount": "100.00", "currency": "EUR"}

    with factory() as db:
        lines = {
            (kind, currency): amount
            for kind, currency, amount in db.execute(
                select(LedgerAccount.kind, JournalLine.currency, JournalLine.amount_minor)
                .join(LedgerAccount, JournalLine.ledger_account_id == LedgerAccount.id)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalEntry.transaction_type == "transfer")
                .where(LedgerAccount.kind == "fx_clearing")
            )
        }
    assert lines == {("fx_clearing", "EUR"): -10000, ("fx_clearing", "TRY"): 350000}
    # Totals stay per currency; nothing converts TRY into EUR.
    totals = {
        t["balance"]["currency"]: t["balance"]["amount"]
        for t in browser.get(f"{ACCOUNTS}/totals").json()
    }
    assert totals == {"TRY": "1500.00", "EUR": "100.00"}
    _entries_balance(factory)


@pytest.mark.parametrize(
    ("amount", "received", "code"),
    [
        ("10.00", "9.00", "amounts_differ"),
        ("0.00", None, "amount_not_positive"),
        ("-5.00", None, "amount_not_positive"),
        ("10.005", None, "invalid_amount"),
        ("10000000000000.01", None, "invalid_amount"),
    ],
)
def test_invalid_transfers_change_nothing(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    amount: str,
    received: str | None,
    code: str,
) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="100.00")
    cash = _account(browser, csrf, "Cash")
    response = _transfer(browser, csrf, bank, cash, amount, received)
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert count(factory, JournalEntry) == 1


def test_transfers_need_two_different_active_accounts(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="100.00")
    empty = _account(browser, csrf, "Empty")
    same = _transfer(browser, csrf, bank, bank, "1.00")
    assert same.json()["code"] == "same_account"
    browser.post(f"{ACCOUNTS}/{empty['id']}/archive", headers=write_headers(csrf))
    archived = _transfer(browser, csrf, bank, empty, "1.00")
    assert (archived.status_code, archived.json()["code"]) == (409, "account_archived")


def test_a_json_number_amount_is_refused(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="100.00")
    cash = _account(browser, csrf, "Cash")
    response = browser.post(
        TRANSFERS,
        json={
            "from_account_id": bank["id"],
            "to_account_id": cash["id"],
            "amount": {"amount": 10.5, "currency": "TRY"},
        },
        headers=_key_headers(csrf),
    )
    assert response.status_code == 422


def test_a_transfer_can_take_an_account_below_zero_with_no_fee(
    browser: TestClient, google: FakeGoogle
) -> None:
    """Assumption #5 and INV-30: allowed, shown as negative, and nothing extra is ever posted."""
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="10.00")
    cash = _account(browser, csrf, "Cash")
    assert _transfer(browser, csrf, bank, cash, "25.00").status_code == 201
    assert _balance(browser, bank) == "-15.00"


def test_concurrent_transfers_all_post_and_balances_add_up(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="100.00")
    cash = _account(browser, csrf, "Cash")
    statuses: list[int] = []
    barrier = threading.Barrier(6)

    def send() -> None:
        barrier.wait()
        statuses.append(_transfer(browser, csrf, bank, cash, "1.25").status_code)

    threads = [threading.Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses == [201] * 6
    assert (_balance(browser, bank), _balance(browser, cash)) == ("92.50", "7.50")
    _entries_balance(factory)


def test_one_key_sent_four_times_at_once_transfers_once(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="100.00")
    cash = _account(browser, csrf, "Cash")
    key = str(uuid.uuid4())
    statuses: list[int] = []
    barrier = threading.Barrier(4)

    def send() -> None:
        barrier.wait()
        statuses.append(_transfer(browser, csrf, bank, cash, "10.00", key=key).status_code)

    threads = [threading.Thread(target=send) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses == [201] * 4
    assert _balance(browser, cash) == "10.00"
    assert count(factory, Transaction) == 2  # the opening balance and one transfer


def test_another_users_accounts_can_be_neither_source_nor_destination(
    browser: TestClient, google: FakeGoogle, auth_app: FastAPI, factory: sessionmaker[Session]
) -> None:
    owner_csrf = sign_in(browser, google, sub="owner-sub", email="owner@example.com")
    theirs = _account(browser, owner_csrf, "Theirs", opening="500.00")

    with TestClient(auth_app, raise_server_exceptions=False) as other:
        csrf = sign_in(other, google, sub="other-sub", email="other@example.com")
        mine = _account(other, csrf, "Mine", opening="5.00")
        assert _transfer(other, csrf, theirs, mine, "100.00").status_code == 404
        assert _transfer(other, csrf, mine, theirs, "1.00").status_code == 404
        assert _adjust(other, csrf, theirs, "0.00", "500.00").status_code == 404
        assert other.get(f"{ACCOUNTS}/{theirs['id']}/activity").status_code == 404
        assert (
            other.get(
                f"{ACCOUNTS}/{theirs['id']}/adjustment-preview",
                params={"target": "1.00", "currency": "TRY"},
            ).status_code
            == 404
        )
        assert other.get(f"{ACCOUNTS}/totals").json() == [
            {"balance": {"amount": "5.00", "currency": "TRY"}, "accounts": 1}
        ]
    assert _balance(browser, theirs) == "500.00"


def test_movements_need_csrf_and_a_key(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="10.00")
    cash = _account(browser, csrf, "Cash")
    body = {
        "from_account_id": bank["id"],
        "to_account_id": cash["id"],
        "amount": {"amount": "1.00", "currency": "TRY"},
    }
    no_csrf = browser.post(TRANSFERS, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    no_key = browser.post(TRANSFERS, json=body, headers=write_headers(csrf))
    assert no_csrf.status_code == 403
    assert (no_key.status_code, no_key.json()["code"]) == (400, "idempotency_key_required")
    assert _balance(browser, bank) == "10.00"


# --- Reading: activity, totals, archiving


def test_activity_is_signed_from_the_accounts_side(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    bank = _account(browser, csrf, "Bank", opening="100.00")
    cash = _account(browser, csrf, "Cash")
    _transfer(browser, csrf, bank, cash, "30.00")
    _adjust(browser, csrf, cash, "25.00", "30.00", reason="Coffee I forgot")

    bank_items = browser.get(f"{ACCOUNTS}/{bank['id']}/activity").json()["items"]
    cash_page = browser.get(f"{ACCOUNTS}/{cash['id']}/activity").json()
    assert [(i["type"], i["amount"]["amount"]) for i in bank_items] == [
        ("transfer", "-30.00"),
        ("opening_balance", "100.00"),
    ]
    assert bank_items[0]["other_account_id"] == cash["id"]
    assert [(i["type"], i["amount"]["amount"]) for i in cash_page["items"]] == [
        ("adjustment", "-5.00"),
        ("transfer", "30.00"),
    ]
    assert cash_page["items"][0]["notes"] == "Coffee I forgot"
    limited = browser.get(f"{ACCOUNTS}/{bank['id']}/activity", params={"limit": 1}).json()
    assert len(limited["items"]) == 1
    assert limited["has_more"] is True


def test_archiving_a_funded_account_takes_an_explicit_step_first(
    browser: TestClient, google: FakeGoogle
) -> None:
    """Archiving needs zero balance: move the money, or adjust to zero, then archive."""
    csrf = sign_in(browser, google)
    old = _account(browser, csrf, "Old bank", opening="80.00")
    new = _account(browser, csrf, "New bank")
    refused = browser.post(f"{ACCOUNTS}/{old['id']}/archive", headers=write_headers(csrf))
    assert refused.json()["code"] == "balance_not_zero"
    _transfer(browser, csrf, old, new, "80.00")
    archived = browser.post(f"{ACCOUNTS}/{old['id']}/archive", headers=write_headers(csrf))
    assert archived.json()["status"] == "archived"
    # Archived balances leave the overview; their history stays.
    assert browser.get(f"{ACCOUNTS}/totals").json() == [
        {"balance": {"amount": "80.00", "currency": "TRY"}, "accounts": 1}
    ]
    assert len(browser.get(f"{ACCOUNTS}/{old['id']}/activity").json()["items"]) == 2
