"""Trip Mode B5: personal links, cash versus share, duplicates, corrections, reports and privacy.

The approved example (FINANCIAL_RULES.md §11): Alice pays €90 for three. Her account moves -€90,
her spending is her €30 share, €60 is owed to her; two €30 settlements bring €60 back as cash,
never as income, and the outstanding balance returns to zero.

People sign in through the real Google routes against the fake Google (MOCKED, tests only).
"""

import random
import threading
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import JournalLine
from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_settlements import _act, _pay
from tests.test_trips import (  # noqa: F401 - fixtures
    API,
    Person,
    _expense,
    _nets,
    _ok,
    _trip,
    people,
)

MONTH = TODAY.replace(day=1)


def _setup(person: Person, currency: str = "EUR", opening: str = "1000.00") -> dict[str, Any]:
    """Runs setup and returns one account with an opening balance."""
    _ok(person.post("/setup", {"currency": currency}, key=False))
    body = {
        "name": f"{person.name} {currency} bank",
        "type": "bank",
        "currency": currency,
        "opening_balance": {"amount": opening, "currency": currency},
    }
    return dict(_ok(person.post("/accounts", body), 201))


def _balance(person: Person, account: dict[str, Any]) -> str:
    return str(_ok(person.get(f"/accounts/{account['id']}"))["balance"]["amount"])


def _link(person: Person, trip: str, account: dict[str, Any], amount: str, status: int = 201,
          key: bool = True, **item: Any) -> Any:  # fmt: skip
    body = {"trip_id": trip, "account_id": account["id"], "amount": amount, **item}
    return _ok(person.post("/links", body, key=key), status)


def _summary(person: Person, code: str = "EUR") -> dict[str, Any]:
    month = _ok(person.get("/summary"))["currencies"]
    return next((c for c in month if c["currency"] == code), {})


def _position(person: Person, trip: str) -> dict[str, str]:
    body = _ok(person.get(f"/me/trips/{trip}/position"))
    return {k: v["amount"] for k, v in body.items() if isinstance(v, dict) and "amount" in v}


def _report(person: Person, code: str = "EUR", **params: Any) -> dict[str, Any]:
    body = _ok(person.get("/reports", **{"from": MONTH.isoformat(), "to": TODAY.isoformat(),
                                          **params}))  # fmt: skip
    return next(c for c in body["by_currency"] if c["currency"] == code)


def _category_id(person: Person, key: str) -> str:
    return str(next(c for c in _ok(person.get("/categories")) if c["system_key"] == key)["id"])


# --- The approved €90 example


def test_ninety_euros_split_three_ways_end_to_end(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    a_bank, b_bank, c_bank = _setup(alice), _setup(bob), _setup(carol)
    trip = _trip(alice, "EUR", [bob, carol])
    dinner = _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    assert dinner["status"] == "approved"

    # Before linking: the share already counts as spending; no cash has been recorded.
    assert _summary(alice)["expenses"]["amount"] == "30.00"
    assert _position(alice, trip)["unrecorded"] == "90.00"

    link = _link(alice, trip, a_bank, "90.00", expense_id=dinner["id"])
    assert (link["kind"], link["status"], link["linked_base_amount"]["amount"]) == (
        "expense_payment", "active", "90.00",
    )  # fmt: skip
    assert link["transaction"]["type"] == "trip_expense_payment"
    assert link["transaction"]["category"] is None and link["review"]["needed"] is False
    assert _balance(alice, a_bank) == "910.00"  # the cash: -€90
    home = _summary(alice)
    assert home["expenses"]["amount"] == "30.00"  # the share, never €90 and never €120
    assert (home["trip_shares"]["amount"], home["trip_paid"]["amount"]) == ("30.00", "90.00")
    assert home["income"]["amount"] == "0.00"
    pos = _position(alice, trip)
    assert (pos["paid"], pos["share"], pos["owed_to_me"], pos["i_owe"]) == (
        "90.00", "30.00", "60.00", "0.00",
    )  # fmt: skip
    assert (pos["cash_position"], pos["linked"], pos["unrecorded"]) == ("90.00", "90.00", "0.00")

    report = _report(alice)
    b = report["balances"]
    # Cash: €90 left the account into the trip; no cash was spent as a plain expense.
    assert (b["trips_out"]["amount"], b["expenses"]["amount"], b["reconciles"]) == (
        "90.00", "0.00", True,
    )  # fmt: skip
    assert report["totals"]["trip_shares"]["amount"] == "30.00"
    assert [t["amount"]["amount"] for t in report["trips"]] == ["30.00"]

    # Bob and Carol each pay €30; Alice confirms and records both as received.
    for person, bank in ((bob, b_bank), (carol, c_bank)):
        paid = _pay(person, trip, alice, "30.00")
        _act(alice, trip, paid["id"], "confirm")
        received = _link(alice, trip, a_bank, "30.00", settlement_id=paid["id"])
        assert received["linked_base_amount"]["amount"] == "-30.00"
        assert received["transaction"]["type"] == "trip_settlement_received"
        sent = _link(person, trip, bank, "30.00", settlement_id=paid["id"])
        assert sent["transaction"]["type"] == "trip_settlement_paid"
        assert _balance(person, bank) == "970.00"
        assert _summary(person)["expenses"]["amount"] == "30.00"  # their share, not +30 more

    assert _balance(alice, a_bank) == "970.00"  # €60 came back
    # Account activity: the payment out is negative, each reimbursement positive.
    activity = _ok(alice.get(f"/accounts/{a_bank['id']}/activity"))["items"]
    assert sorted(
        (i["type"], i["amount"]["amount"]) for i in activity if i["type"] != "opening_balance"
    ) == [
        ("trip_expense_payment", "-90.00"),
        ("trip_settlement_received", "30.00"),
        ("trip_settlement_received", "30.00"),
    ]
    home = _summary(alice)
    assert (home["income"]["amount"], home["expenses"]["amount"]) == ("0.00", "30.00")
    assert home["trip_received"]["amount"] == "60.00"
    assert _nets(alice, trip) == {"Alice": "0.00", "Bob": "0.00", "Carol": "0.00"}
    pos = _position(alice, trip)
    assert (pos["owed_to_me"], pos["cash_position"], pos["linked"], pos["unrecorded"]) == (
        "0.00", "30.00", "30.00", "0.00",
    )  # fmt: skip
    b = _report(alice)["balances"]
    assert (b["income"]["amount"], b["trips_in"]["amount"], b["trips_out"]["amount"]) == (
        "0.00", "60.00", "90.00",
    )  # fmt: skip
    assert b["reconciles"] is True
    summary = _ok(alice.get(f"/trips/{trip}/reports/summary"))
    assert (summary["outstanding"]["amount"], summary["total"]["amount"]) == ("0.00", "90.00")
    assert summary["suggestions"] == []

    # The view filter: shares included by default, left out, or alone. Income never changes.
    def spent(view: str) -> str:
        return str(_report(alice, trip_allocation=view)["totals"]["expenses"]["amount"])

    assert (spent("include"), spent("exclude"), spent("only")) == ("30.00", "0.00", "30.00")
    insights = _ok(alice.get("/insights", trip_allocation="exclude"))
    assert insights["totals"] is None or insights["totals"]["expenses"]["amount"] == "0.00"
    csv = alice.get("/reports/trip-shares.csv", **{"from": MONTH.isoformat(),
                                                    "to": TODAY.isoformat()})  # fmt: skip
    assert csv.status_code == 200 and csv.text.count("\r\n") == 2  # header + one share
    assert "30.00" in csv.text and "Lisbon" in csv.text


def test_budgets_always_count_shares_in_the_matching_category(people: list[Person]) -> None:
    alice, bob, _, _ = people
    _setup(alice)
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "80.00", [alice, bob])  # food_and_drinks, €40 each
    new = {"kind": "expense", "name": "Trips", "icon": "tag", "color": "sky"}
    custom = _ok(alice.post("/categories", new, key=False), 201)
    for category in (None, _category_id(alice, "food_and_drinks"), custom["id"]):
        body = {
            "category_id": category,
            "limit": {"amount": "100.00", "currency": "EUR"},
            "from_month": MONTH.strftime("%Y-%m"),
        }
        _ok(alice.c.put(f"{API}/budgets", json=body, headers=alice.h(False)))
    budgets = _ok(alice.get("/budgets", month=MONTH.strftime("%Y-%m")))["budgets"]
    spent = {
        (b["category"]["system_key"] or b["category"]["name"]) if b["category"] else None:
        b["spent"]["amount"]
        for b in budgets
    }  # fmt: skip
    assert spent == {None: "40.00", "food_and_drinks": "40.00", "Trips": "0.00"}
    detail = _ok(alice.get("/budgets/detail", currency="EUR", month=MONTH.strftime("%Y-%m"),
                           category_id=_category_id(alice, "food_and_drinks")))  # fmt: skip
    assert [s["amount"]["amount"] for s in detail["trip_shares"]] == ["40.00"]
    assert detail["transactions"] == []
    # Bob paid nothing and linked nothing: his share still counts (§11.10).
    _ok(bob.post("/setup", {"currency": "EUR"}, key=False))
    assert _summary(bob)["expenses"]["amount"] == "40.00"


# --- Who may link what


def test_only_the_real_payer_links_and_never_to_someone_elses_account(
    people: list[Person],
) -> None:
    alice, bob, carol, dan = people
    a_bank, b_bank, d_bank = _setup(alice), _setup(bob), _setup(dan)
    trip = _trip(alice, "EUR", [bob, carol])
    dinner = _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    # A beneficiary can't record someone else's payment; nobody links a share.
    refused = bob.post("/links", {"trip_id": trip, "expense_id": dinner["id"],
                                  "account_id": b_bank["id"], "amount": "30.00"})  # fmt: skip
    assert (refused.status_code, refused.json()["code"]) == (403, "not_payer")
    # Alice can't post to Bob's account: it isn't hers (404, so ids can't be probed).
    other = alice.post("/links", {"trip_id": trip, "expense_id": dinner["id"],
                                  "account_id": b_bank["id"], "amount": "90.00"})  # fmt: skip
    assert other.status_code == 404
    # Someone who never belonged: 404.
    outsider = dan.post("/links", {"trip_id": trip, "expense_id": dinner["id"],
                                   "account_id": d_bank["id"], "amount": "90.00"})  # fmt: skip
    assert outsider.status_code == 404
    # A pending item can't be linked: proposed by Bob, not yet approved by Carol (the payer).
    proposed = _expense(bob, trip, carol, "12.00", [bob, carol])
    assert proposed["status"] == "pending_payer"
    pending = carol.post("/links", {"trip_id": trip, "expense_id": proposed["id"],
                                    "account_id": a_bank["id"], "amount": "12.00"})  # fmt: skip
    assert pending.status_code in (403, 404, 409)
    # The idempotency key replays the same link; a new key is refused as already linked.
    key = str(uuid.uuid4())
    body = {"trip_id": trip, "expense_id": dinner["id"], "account_id": a_bank["id"],
            "amount": "90.00"}  # fmt: skip
    headers = {**alice.h(False), "Idempotency-Key": key}
    first = alice.c.post(f"{API}/links", json=body, headers=headers)
    again = alice.c.post(f"{API}/links", json=body, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert again.headers.get("Idempotent-Replayed") == "true"
    dup = alice.post("/links", body)
    assert (dup.status_code, dup.json()["code"]) == (409, "already_linked")
    assert _balance(alice, a_bank) == "910.00"  # posted once
    link_id = first.json()["id"]
    # Owner-only: others get 404 and see nothing in their own list.
    assert bob.get(f"/links/{link_id}").status_code == 404
    gone = bob.c.delete(f"{API}/links/{link_id}", params={"personal_transaction": "reverse"},
                        headers=bob.h(False))  # fmt: skip
    assert gone.status_code == 404
    assert _ok(bob.get("/links")) == []
    assert _ok(bob.get(f"/me/trips/{trip}/position"))["linked"]["amount"] == "0.00"
    # A trip entry can't be voided from the transaction list: it's unlinked instead.
    txn = first.json()["transaction"]
    void = alice.c.post(f"{API}/transactions/{txn['id']}/void",
                        headers={**alice.h(False), "If-Match": f'"{txn["version"]}"'})  # fmt: skip
    assert (void.status_code, void.json()["code"]) == (409, "managed_by_trip_link")
    # No CSRF token: refused before anything happens.
    no_csrf = alice.c.post(f"{API}/links", json=body, headers={"Idempotency-Key": "x" * 20})
    assert no_csrf.status_code in (401, 403)


def test_trip_responses_never_reveal_links_or_accounts(people: list[Person]) -> None:
    alice, bob, _, _ = people
    marker = "PRIVATE-LINK-9c1e"
    _ok(alice.post("/setup", {"currency": "EUR"}, key=False))
    bank = _ok(alice.post("/accounts", {"name": f"{marker} bank", "type": "bank",
                                         "currency": "EUR",
                                         "opening_balance": {"amount": "777.00",
                                                             "currency": "EUR"}}), 201)  # fmt: skip
    trip = _trip(alice, "EUR", [bob])
    dinner = _expense(alice, trip, alice, "50.00", [alice, bob])
    link = _link(alice, trip, bank, "50.00", expense_id=dinner["id"], description=marker)
    secrets = [marker, link["id"], link["transaction"]["id"], "777.00", "727.00", bank["id"]]
    paths = [
        "", "/members", "/expenses", f"/expenses/{dinner['id']}", "/dashboard", "/activity",
        "/balances", "/settlements", "/settlements/suggestions", "/disputes",
        "/reports/summary", "/reports/expenses.csv", "/reports/balances.csv",
        "/reports/settlements.csv",
    ]  # fmt: skip
    for person in (alice, bob):
        for path in paths:
            body = person.get(f"/trips/{trip}{path}")
            assert body.status_code == 200, (path, body.text)
            for secret in secrets:
                assert secret not in body.text, (person.name, path)


# --- Attach existing, duplicates, dismissals


def test_attach_existing_replaces_a_hand_entry_without_double_counting(
    people: list[Person],
) -> None:
    alice, bob, carol, _ = people
    a_bank, b_bank = _setup(alice), _setup(bob)
    trip = _trip(alice, "EUR", [bob, carol])
    # Alice already recorded the €90 dinner by hand; Bob recorded his €30 share by hand.
    manual = _ok(alice.post("/transactions", {
        "type": "expense", "account_id": a_bank["id"],
        "amount": {"amount": "90.00", "currency": "EUR"},
        "category_id": _category_id(alice, "food_and_drinks"), "description": "Dinner",
    }), 201)  # fmt: skip
    mine = _ok(bob.post("/transactions", {
        "type": "expense", "account_id": b_bank["id"],
        "amount": {"amount": "30.00", "currency": "EUR"},
        "category_id": _category_id(bob, "food_and_drinks"),
    }), 201)  # fmt: skip
    dinner = _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    assert _summary(alice)["expenses"]["amount"] == "120.00"  # the double count to resolve

    found = _ok(alice.get("/me/possible-duplicates", trip_id=trip))
    assert [(g["expense_id"], g["paid_by_me"]) for g in found] == [(dinner["id"], True)]
    (candidate,) = found[0]["candidates"]
    assert (candidate["transaction"]["id"], candidate["strength"], candidate["match"]) == (
        manual["id"], "strong", "original",
    )  # fmt: skip
    body = {"trip_id": trip, "expense_id": dinner["id"], "mode": "attach",
            "transaction_id": manual["id"], "transaction_version": manual["version"]}  # fmt: skip
    unconfirmed = alice.post("/links", body)
    assert (unconfirmed.status_code, unconfirmed.json()["code"]) == (422, "confirmation_required")
    attached = _ok(alice.post("/links", {**body, "confirm": True}), 201)
    assert attached["transaction"]["replaces_transaction_id"] == manual["id"]
    original = _ok(alice.get(f"/transactions/{manual['id']}"))
    assert original["status"] == "reversed"  # kept in history
    assert _balance(alice, a_bank) == "910.00"  # unchanged by attaching
    assert _summary(alice)["expenses"]["amount"] == "30.00"
    assert _ok(alice.get("/me/possible-duplicates", trip_id=trip)) == []
    # Attaching the same entry twice is refused.
    again = alice.post("/links", {**body, "confirm": True})
    assert again.status_code == 409

    # Bob's hand entry matches his share: a possible duplicate he can dismiss.
    (group,) = _ok(bob.get("/me/possible-duplicates"))
    assert group["paid_by_me"] is False and group["my_share"]["amount"] == "30.00"
    assert [(c["transaction"]["id"], c["match"]) for c in group["candidates"]] == [
        (mine["id"], "share")
    ]
    _ok(
        bob.post(
            "/me/possible-duplicates/dismiss",
            {"transaction_id": mine["id"], "expense_id": dinner["id"]},
            key=False,
        ),
        204,
    )
    assert _ok(bob.get("/me/possible-duplicates")) == []
    assert _summary(bob)["expenses"]["amount"] == "60.00"  # dismissing changes no figure
    # Someone else's transaction can't be dismissed or attached.
    nope = alice.post(
        "/me/possible-duplicates/dismiss",
        {"transaction_id": mine["id"], "expense_id": dinner["id"]},
        key=False,
    )
    assert nope.status_code == 404


# --- Corrections and reversals: computed review, never automatic


def _correct(by: Person, trip: str, expense: dict[str, Any], amount: str,
             people: list[Person]) -> Any:  # fmt: skip
    body = {
        "description": "Dinner", "category_key": "food_and_drinks",
        "expense_date": TODAY.isoformat(), "amount": {"amount": amount, "currency": "EUR"},
        "split": {"method": "equal", "shares": [{"user_id": p.id} for p in people]},
    }  # fmt: skip
    return _ok(by.post(f"/trips/{trip}/expenses/{expense['id']}/versions", body), 201)


def test_a_correction_asks_for_review_and_never_posts_by_itself(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    bank = _setup(alice)
    trip = _trip(alice, "EUR", [bob, carol])
    dinner = _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    link = _link(alice, trip, bank, "90.00", expense_id=dinner["id"])
    _correct(alice, trip, dinner, "60.00", [alice, bob, carol])
    assert _balance(alice, bank) == "910.00"  # nothing posted automatically
    state = _ok(alice.get(f"/links/{link['id']}"))["review"]
    assert (state["needed"], state["reason"], state["current_base_amount"]["amount"]) == (
        True, "expense_changed", "60.00",
    )  # fmt: skip
    pos = _position(alice, trip)
    assert (pos["share"], pos["cash_position"], pos["unrecorded"]) == ("20.00", "60.00", "-30.00")
    assert _summary(alice)["expenses"]["amount"] == "20.00"  # the share follows the correction
    # Only the owner resolves.
    assert bob.post(f"/links/{link['id']}/resolve", {"action": "keep"}).status_code == 404
    updated = _ok(alice.post(f"/links/{link['id']}/resolve",
                             {"action": "update", "amount": "60.00"}))  # fmt: skip
    assert updated["review"]["needed"] is False
    assert updated["linked_base_amount"]["amount"] == "60.00"
    assert _balance(alice, bank) == "940.00"
    assert _position(alice, trip)["unrecorded"] == "0.00"

    # Keep as is: the review clears, the difference stays visible as unrecorded.
    _correct(alice, trip, dinner, "75.00", [alice, bob, carol])
    kept = _ok(alice.post(f"/links/{link['id']}/resolve", {"action": "keep"}))
    assert kept["review"]["needed"] is False and kept["linked_base_amount"]["amount"] == "60.00"
    assert _position(alice, trip)["unrecorded"] == "15.00"
    assert _balance(alice, bank) == "940.00"

    # Cancelled: the share disappears, the link asks again; reversing restores the account.
    _ok(alice.post(f"/trips/{trip}/expenses/{dinner['id']}/cancel"), 201)
    state = _ok(alice.get(f"/links/{link['id']}"))["review"]
    assert (state["needed"], state["reason"]) == (True, "expense_cancelled")
    assert _summary(alice).get("expenses", {"amount": "0.00"})["amount"] == "0.00"
    no_update = alice.post(f"/links/{link['id']}/resolve", {"action": "update", "amount": "1.00"})
    assert no_update.status_code == 409
    reversed_ = _ok(alice.post(f"/links/{link['id']}/resolve", {"action": "reverse"}))
    assert reversed_["status"] == "unlinked"
    assert _balance(alice, bank) == "1000.00"
    assert _position(alice, trip)["linked"] == "0.00"


def test_a_reversed_settlement_asks_its_linker_to_review(people: list[Person]) -> None:
    alice, bob, _, _ = people
    a_bank, b_bank = _setup(alice), _setup(bob)
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "60.00", [alice, bob])
    paid = _pay(bob, trip, alice, "30.00")
    # Not yet confirmed: nothing to link.
    early = alice.post("/links", {"trip_id": trip, "settlement_id": paid["id"],
                                  "account_id": a_bank["id"], "amount": "30.00"})  # fmt: skip
    assert (early.status_code, early.json()["code"]) == (409, "settlement_not_confirmed")
    _act(alice, trip, paid["id"], "confirm")
    received = _link(alice, trip, a_bank, "30.00", settlement_id=paid["id"])
    sent = _link(bob, trip, b_bank, "30.00", settlement_id=paid["id"])
    assert _balance(alice, a_bank) == "1030.00" and _balance(bob, b_bank) == "970.00"
    reversal = _act(alice, trip, paid["id"], "reverse", status=201)
    # A reversal itself is never linked.
    refused = alice.post("/links", {"trip_id": trip, "settlement_id": reversal["id"],
                                    "account_id": a_bank["id"], "amount": "30.00"})  # fmt: skip
    assert refused.status_code in (409,)
    _act(bob, trip, reversal["id"], "confirm")
    for person, link in ((alice, received), (bob, sent)):
        state = _ok(person.get(f"/links/{link['id']}"))["review"]
        assert (state["needed"], state["reason"]) == (True, "settlement_reversed")
    assert _balance(alice, a_bank) == "1030.00"  # never automatic
    _ok(alice.post(f"/links/{received['id']}/resolve", {"action": "reverse"}))
    _ok(bob.post(f"/links/{sent['id']}/resolve", {"action": "keep"}))
    assert _balance(alice, a_bank) == "1000.00" and _balance(bob, b_bank) == "970.00"
    assert _ok(bob.get(f"/links/{sent['id']}"))["review"]["needed"] is False
    # Income never moved at any point.
    assert _report(alice)["balances"]["income"]["amount"] == "0.00"


def test_unlinking_converts_back_or_reverses(people: list[Person]) -> None:
    alice, bob, _, _ = people
    bank = _setup(alice)
    trip = _trip(alice, "EUR", [bob])
    dinner = _expense(alice, trip, alice, "40.00", [alice, bob])
    link = _link(alice, trip, bank, "40.00", expense_id=dinner["id"])
    bad = alice.c.delete(f"{API}/links/{link['id']}", params={"personal_transaction": "x"},
                         headers=alice.h(False))  # fmt: skip
    assert bad.status_code == 422
    done = _ok(alice.c.delete(f"{API}/links/{link['id']}",
                              params={"personal_transaction": "convert"},
                              headers=alice.h(False)))  # fmt: skip
    assert done["status"] == "unlinked"
    assert _balance(alice, bank) == "960.00"  # the cash stays recorded, now as a plain expense
    plain = _ok(alice.get("/transactions", type="expense"))["items"]
    assert [(p["amount"]["amount"], p["category"]["system_key"]) for p in plain] == [
        ("40.00", "food_and_drinks")
    ]
    assert plain[0]["replaces_transaction_id"] == link["transaction"]["id"]
    # Unlinking again changes nothing.
    again = _ok(alice.c.delete(f"{API}/links/{link['id']}",
                               params={"personal_transaction": "reverse"},
                               headers=alice.h(False)))  # fmt: skip
    assert again["status"] == "unlinked" and _balance(alice, bank) == "960.00"
    # Linking afresh after unlinking is allowed (one live link at a time).
    relinked = _link(alice, trip, bank, "40.00", expense_id=dinner["id"])
    assert relinked["status"] == "active"


# --- Currencies


def test_another_currency_keeps_trip_base_and_actual_charge(
    people: list[Person], factory: "sessionmaker[Session]"
) -> None:
    alice, bob, _, _ = people
    _ok(alice.post("/setup", {"currency": "TRY"}, key=False))
    lira = _ok(alice.post("/accounts", {"name": "Lira card", "type": "bank", "currency": "TRY",
                                         "opening_balance": {"amount": "10000.00",
                                                             "currency": "TRY"}}), 201)  # fmt: skip
    trip = _trip(alice, "EUR", [bob])
    dinner = _expense(alice, trip, alice, "90.00", [alice, bob])
    # The statement says 3,150.00 TRY: the amount charged is what's posted; no rate is invented.
    link = _link(alice, trip, lira, "3150.00", expense_id=dinner["id"])
    txn = link["transaction"]
    assert (txn["amount"]["amount"], txn["amount"]["currency"]) == ("3150.00", "TRY")
    assert txn["original_amount"] == {"amount": "90.00", "currency": "EUR"}
    assert (txn["fx"]["method"], txn["fx"]["rate"], txn["fx"]["source"]) == (
        "actual_amount", "35", None,
    )  # fmt: skip
    assert link["linked_base_amount"] == {"amount": "90.00", "currency": "EUR"}
    assert _balance(alice, lira) == "6850.00"
    pos = _position(alice, trip)
    assert (pos["linked"], pos["unrecorded"], pos["share"]) == ("90.00", "0.00", "45.00")
    # The share is in EUR (never converted); the TRY block has cash only, and reconciles.
    assert _summary(alice, "EUR")["expenses"]["amount"] == "45.00"
    try_block = _report(alice, "TRY")
    assert try_block["totals"]["expenses"]["amount"] == "0.00"
    assert try_block["balances"]["trips_out"]["amount"] == "3150.00"
    assert try_block["balances"]["reconciles"] is True
    with factory() as db:
        lines = db.execute(text(
            "SELECT la.kind, jl.currency, sum(jl.amount_minor) FROM journal_lines jl "
            "JOIN ledger_accounts la ON la.id = jl.ledger_account_id "
            "WHERE la.kind = 'trip_clearing' GROUP BY la.kind, jl.currency"
        )).all()  # fmt: skip
    assert [(k, c, int(str(s))) for k, c, s in lines] == [("trip_clearing", "TRY", 315000)]


# --- Concurrency


def test_concurrent_links_post_once(people: list[Person]) -> None:
    alice, bob, _, _ = people
    bank = _setup(alice)
    trip = _trip(alice, "EUR", [bob])
    dinner = _expense(alice, trip, alice, "50.00", [alice, bob])
    body = {"trip_id": trip, "expense_id": dinner["id"], "account_id": bank["id"],
            "amount": "50.00"}  # fmt: skip
    results: list[int] = []
    barrier = threading.Barrier(4)

    def go() -> None:
        barrier.wait()
        results.append(alice.post("/links", body).status_code)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 409, 409, 409]
    assert _balance(alice, bank) == "950.00"


# --- Randomized sequences, reconciled independently


@pytest.mark.parametrize("seed", [3, 11, 29])
def test_random_sequences_reconcile(
    people: list[Person], factory: "sessionmaker[Session]", seed: int
) -> None:
    """Random expenses, payments and links; every figure recomputed here from first principles."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test data
    alice, bob, carol, _ = people
    group = [alice, bob, carol]
    banks = {p.name: _setup(p, opening="5000.00") for p in group}
    trip = _trip(alice, "EUR", [bob, carol])
    paid: dict[str, int] = defaultdict(int)  # cents, base EUR
    share: dict[str, int] = defaultdict(int)
    sent: dict[str, int] = defaultdict(int)
    received: dict[str, int] = defaultdict(int)
    linked: dict[str, int] = defaultdict(int)
    cash: dict[str, int] = defaultdict(int)  # change in each person's bank, cents
    for _ in range(10):
        action = rng.random()
        if action < 0.55:
            payer = rng.choice(group)
            cents = rng.randint(1, 30000)
            who = rng.sample(group, rng.randint(1, 3))
            amount = f"{Decimal(cents) / 100:.2f}"
            expense = _expense(payer, trip, payer, amount, who)
            paid[payer.name] += cents
            for s in expense["current_version"]["shares"]:
                name = next(p.name for p in group if p.id == s["user_id"])
                share[name] += int(Decimal(s["base_share"]["amount"]) * 100)
            if rng.random() < 0.6:
                _link(payer, trip, banks[payer.name], amount, expense_id=expense["id"])
                linked[payer.name] += cents
                cash[payer.name] -= cents
        else:
            payer, to = rng.sample(group, 2)
            cents = rng.randint(1, 5000)
            amount = f"{Decimal(cents) / 100:.2f}"
            settlement = _pay(payer, trip, to, amount, confirm_duplicate=True)
            if rng.random() < 0.8:
                _act(to, trip, settlement["id"], "confirm")
                sent[payer.name] += cents
                received[to.name] += cents
                if rng.random() < 0.5:
                    _link(payer, trip, banks[payer.name], amount, settlement_id=settlement["id"])
                    linked[payer.name] += cents
                    cash[payer.name] -= cents
                if rng.random() < 0.5:
                    _link(to, trip, banks[to.name], amount, settlement_id=settlement["id"])
                    linked[to.name] -= cents
                    cash[to.name] += cents
        nets = {p.name: paid[p.name] - share[p.name] + sent[p.name] - received[p.name]
                for p in group}  # fmt: skip
        assert sum(nets.values()) == 0
        for p in group:
            pos = _ok(p.get(f"/me/trips/{trip}/position"))

            def c(field: str, body: dict[str, Any] = pos) -> int:
                return int(Decimal(body[field]["amount"]) * 100)

            assert (c("paid"), c("share"), c("net")) == (paid[p.name], share[p.name],
                                                         nets[p.name])  # fmt: skip
            assert c("cash_position") == share[p.name] + nets[p.name]
            assert c("linked") == linked[p.name]
            assert c("unrecorded") == share[p.name] + nets[p.name] - linked[p.name]
            balance = int(Decimal(_balance(p, banks[p.name])) * 100)
            assert balance == 500000 + cash[p.name]
            home = _summary(p)
            assert (
                int(Decimal(home.get("expenses", {"amount": "0"})["amount"]) * 100) == share[p.name]
            )
            assert home.get("income", {"amount": "0.00"})["amount"] == "0.00"
            assert _report(p)["balances"]["reconciles"] is True
    with factory() as db:
        sums: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        for entry_id, currency, minor in db.execute(
            select(JournalLine.entry_id, JournalLine.currency, JournalLine.amount_minor)
        ):
            sums[(entry_id, currency)] += minor
    assert all(total == 0 for total in sums.values())


# --- The trip report


def test_trip_report_counts_only_approved_and_filters_honestly(
    people: list[Person], monkeypatch: pytest.MonkeyPatch, auth_app: FastAPI
) -> None:
    from app.services import exports

    alice, bob, carol, dan = people
    trip = _trip(alice, "EUR", [bob, carol])
    _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    _expense(bob, trip, bob, "40.00", [alice, bob], description="=HYPERLINK(1)")
    hotel = _expense(alice, trip, alice, "200.00", [alice, bob])
    _ok(alice.post(f"/trips/{trip}/expenses/{hotel['id']}/cancel"), 201)  # never counted
    proposed = _expense(bob, trip, alice, "15.00", [alice, bob])  # pending: never counted
    assert proposed["status"] == "pending_payer"
    paid = _pay(carol, trip, alice, "30.00")
    _act(alice, trip, paid["id"], "confirm")
    _pay(bob, trip, alice, "5.00")  # pending: listed apart, not counted

    r = _ok(alice.get(f"/trips/{trip}/reports/summary"))
    assert (r["total"]["amount"], r["expense_count"], r["pending_expenses"]) == ("130.00", 2, 1)
    assert [(c["category_key"], c["amount"]["amount"]) for c in r["by_category"]] == [
        ("food_and_drinks", "130.00")
    ]
    rows = {m["display_name"]: m for m in r["members"]}
    assert {n: (m["paid"]["amount"], m["benefited"]["amount"], m["net"]["amount"])
            for n, m in rows.items()} == {
        "Alice": ("90.00", "50.00", "10.00"),
        "Bob": ("40.00", "50.00", "-10.00"),
        "Carol": ("0.00", "30.00", "0.00"),
    }  # fmt: skip
    assert rows["Bob"]["pending_sent"]["amount"] == "5.00"
    assert r["outstanding"]["amount"] == "10.00" and r["balanced"] is True
    assert [s["amount"]["amount"] for s in r["settlements_confirmed"]] == ["30.00"]
    assert [s["amount"]["amount"] for s in r["settlements_pending"]] == ["5.00"]
    assert [(s["payer"]["display_name"], s["amount"]["amount"]) for s in r["suggestions"]] == [
        ("Bob", "10.00")
    ]
    # Filters narrow the expenses; balances stay whole-trip.
    by_carol = _ok(alice.get(f"/trips/{trip}/reports/summary", member_id=carol.id))
    assert (by_carol["total"]["amount"], by_carol["filtered"]) == ("90.00", True)
    assert {m["display_name"]: m["net"]["amount"] for m in by_carol["members"]}["Bob"] == "-10.00"
    other = _ok(alice.get(f"/trips/{trip}/reports/summary", category="travel"))
    assert other["total"]["amount"] == "0.00" and other["expenses"] == []
    assert alice.get(f"/trips/{trip}/reports/summary", category="nope").status_code == 422
    # Someone who never belonged: 404, for the summary and every file.
    for path in ("summary", "expenses.csv", "balances.csv", "settlements.csv", "export.pdf"):
        assert dan.get(f"/trips/{trip}/reports/{path}").status_code == 404
    # CSV: spreadsheet formulas are neutralised; the share column lists who shared.
    csv = alice.get(f"/trips/{trip}/reports/expenses.csv")
    assert csv.status_code == 200 and "'=HYPERLINK(1)" in csv.text
    assert "Alice 30.00; Bob 30.00; Carol 30.00" in csv.text
    balances_csv = alice.get(f"/trips/{trip}/reports/balances.csv").text
    assert "Bob,member,active,40.00,50.00,40.00,50.00,0.00,0.00,5.00,0.00,-10.00,EUR" in (
        balances_csv
    )
    # The PDF says when rows were left out, like the personal report.
    captured: dict[str, str] = {}

    def fake_pdf(html: str, *_: Any) -> bytes:
        captured["html"] = html
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(exports, "render_pdf", fake_pdf)
    auth_app.state.settings = auth_app.state.settings.model_copy(update={"pdf_max_rows": 1})
    partial = alice.get(f"/trips/{trip}/reports/export.pdf")
    assert partial.headers["X-Report-Complete"] == "false"
    assert (partial.headers["X-Report-Rows-Included"], partial.headers["X-Report-Rows-Total"]) == (
        "1", "2",
    )  # fmt: skip
    assert "PARTIAL REPORT" in captured["html"] and "Suggested, not paid" in captured["html"]
    auth_app.state.settings = auth_app.state.settings.model_copy(update={"pdf_max_rows": 2000})
    arabic = alice.get(f"/trips/{trip}/reports/export.pdf", locale="ar")
    assert arabic.headers["X-Report-Complete"] == "true"
    assert 'dir="rtl"' in captured["html"] and "تقرير الرحلة" in captured["html"]


def test_a_former_member_sees_only_their_part(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    _expense(alice, trip, alice, "60.00", [alice, bob])
    _expense(carol, trip, carol, "20.00", [carol], description="Carol's own")
    removed = alice.c.delete(f"{API}/trips/{trip}/members/{carol.id}", headers=alice.h(False))
    assert removed.status_code in (200, 204), removed.text
    r = _ok(carol.get(f"/trips/{trip}/reports/summary"))
    assert r["scope"] == "mine"
    assert [e["description"] for e in r["expenses"]] == ["Carol's own"]
    assert [m["display_name"] for m in r["members"]] == ["Carol"]
    assert r["suggestions"] == [] and r["total"]["amount"] == "20.00"
    full = _ok(alice.get(f"/trips/{trip}/reports/summary"))
    carol_row = next(m for m in full["members"] if m["display_name"] == "Carol")
    assert (full["scope"], carol_row["status"]) == ("full", "removed")


def test_trip_report_pdf_renders_in_a_real_browser(people: list[Person]) -> None:
    alice, bob, _, _ = people
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "90.00", [alice, bob])
    for locale in ("en", "ar", "tr"):
        response = alice.get(f"/trips/{trip}/reports/export.pdf", locale=locale)
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"%PDF-")
        assert response.headers["X-Report-Complete"] == "true"
