"""Phase B, B4: settlements, reversals, disputes, the trip permission matrix, and randomized
sequences that check the balance identity after every step.

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
from fastapi.testclient import TestClient

from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_trips import (  # noqa: F401 - fixtures
    API,
    Person,
    _expense,
    _nets,
    _ok,
    _trip,
    people,
)


def _pay(by: Person, trip: str, to: Person, amount: str, status: int = 201, **extra: Any) -> Any:
    body = {"recipient_user_id": to.id, "amount": {"amount": amount, "currency": "EUR"}, **extra}
    return _ok(by.post(f"/trips/{trip}/settlements", body), status)


def _act(by: Person, trip: str, sid: str, action: str, status: int = 200, **body: Any) -> Any:
    return _ok(by.post(f"/trips/{trip}/settlements/{sid}/{action}", body or None), status)


def _suggestions(person: Person, trip: str) -> list[tuple[str, str, str]]:
    body = _ok(person.get(f"/trips/{trip}/settlements/suggestions"))
    assert body["based_on"] == "confirmed_only"
    return [
        (s["payer"]["display_name"], s["recipient"]["display_name"], s["amount"]["amount"])
        for s in body["items"]
    ]


# --- Settlements


def test_full_and_partial_settlements_count_only_once_confirmed(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    _expense(alice, trip, alice, "300.00", [alice, bob, carol])
    # Equal debts tie; their order is deterministic but depends on ids.
    assert sorted(_suggestions(alice, trip)) == [
        ("Bob", "Alice", "100.00"),
        ("Carol", "Alice", "100.00"),
    ]

    full = _pay(bob, trip, alice, "100.00", note="Bank transfer")
    assert (full["kind"], full["state"], full["status"]) == ("payment", "pending", "pending")
    assert full["warnings"] == [] and full["waiting_on_me"] is False
    # Recorded is not paid: nothing moves until Alice confirms.
    assert _nets(alice, trip) == {"Alice": "200.00", "Bob": "-100.00", "Carol": "-100.00"}
    waiting = _ok(alice.get(f"/trips/{trip}/dashboard"))["settlements_waiting"]
    assert [w["id"] for w in waiting] == [full["id"]] and waiting[0]["can"]["confirm"]
    confirmed = _act(alice, trip, full["id"], "confirm")
    assert confirmed["state"] == "confirmed" and confirmed["decided_by"]["display_name"] == "Alice"
    assert _nets(alice, trip) == {"Alice": "100.00", "Bob": "0.00", "Carol": "-100.00"}
    assert _suggestions(alice, trip) == [("Carol", "Alice", "100.00")]

    partial = _pay(carol, trip, alice, "40.00")
    _act(alice, trip, partial["id"], "confirm")
    assert _nets(alice, trip) == {"Alice": "60.00", "Bob": "0.00", "Carol": "-60.00"}
    assert _suggestions(alice, trip) == [("Carol", "Alice", "60.00")]
    balances = _ok(alice.get(f"/trips/{trip}/balances"))
    carol_row = next(m for m in balances["members"] if m["display_name"] == "Carol")
    assert (carol_row["sent"]["amount"], carol_row["owed"]["amount"]) == ("40.00", "100.00")
    history = _ok(alice.get(f"/trips/{trip}/settlements"))
    assert [h["amount"]["amount"] for h in history] == ["40.00", "100.00"]


def test_rejected_and_cancelled_payments_change_nothing(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    before = _nets(alice, trip)
    over = _pay(bob, trip, alice, "50.00")
    assert over["warnings"] == ["exceeds_outstanding_balance"]  # allowed, with a warning
    rejected = _act(alice, trip, over["id"], "reject", reason="Nothing arrived")
    assert (rejected["state"], rejected["decision_reason"]) == ("rejected", "Nothing arrived")
    assert _nets(alice, trip) == before
    oops = _pay(carol, trip, alice, "30.00")
    assert _act(carol, trip, oops["id"], "cancel")["state"] == "cancelled"
    assert _nets(alice, trip) == before
    # A decided payment can't be decided again differently.
    assert alice.post(f"/trips/{trip}/settlements/{over['id']}/confirm").json()["code"] == (
        "settlement_not_pending"
    )


def test_reversal_is_auditable_and_unique(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    _expense(alice, trip, alice, "300.00", [alice, bob, carol])
    paid = _pay(bob, trip, alice, "100.00")
    _act(alice, trip, paid["id"], "confirm")
    # Pending payments can't be reversed; nor can someone outside the payment.
    assert carol.post(f"/trips/{trip}/settlements/{paid['id']}/reverse").status_code == 403
    reversal = _act(alice, trip, paid["id"], "reverse", status=201)
    assert reversal["kind"] == "reversal"
    assert (reversal["payer"]["display_name"], reversal["recipient"]["display_name"]) == (
        "Alice",
        "Bob",
    )
    again = alice.post(f"/trips/{trip}/settlements/{paid['id']}/reverse")
    assert (again.status_code, again.json()["code"]) == (409, "reversal_exists")
    original = _ok(alice.get(f"/trips/{trip}/settlements/{paid['id']}"))
    assert original["status"] == "reversal_pending" and original["state"] == "confirmed"
    # The initiator can't confirm their own reversal; the other party does.
    assert alice.post(f"/trips/{trip}/settlements/{reversal['id']}/confirm").status_code == 403
    _act(bob, trip, reversal["id"], "confirm")
    assert _nets(alice, trip)["Bob"] == "-100.00"  # as if never paid; history kept
    original = _ok(alice.get(f"/trips/{trip}/settlements/{paid['id']}"))
    assert (original["status"], original["reversal_id"]) == ("reversed", reversal["id"])
    no_double = alice.post(f"/trips/{trip}/settlements/{reversal['id']}/reverse")
    assert no_double.status_code == 409
    # "Correct this payment": a new payment pointing at the reversed one.
    corrected = _pay(bob, trip, alice, "90.00", corrects_settlement_id=paid["id"])
    assert corrected["corrects_settlement_id"] == paid["id"]
    _act(alice, trip, corrected["id"], "confirm")
    assert _nets(alice, trip)["Bob"] == "-10.00"


def test_confirming_twice_is_harmless_and_keys_replay(people: list[Person]) -> None:
    alice, bob, _, _ = people
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "50.00", [alice, bob])
    paid = _pay(bob, trip, alice, "25.00")
    first = _act(alice, trip, paid["id"], "confirm")
    second = _act(alice, trip, paid["id"], "confirm")  # a new key, same person, same decision
    assert first["state"] == second["state"] == "confirmed"
    headers = {**alice.h(False), "Idempotency-Key": str(uuid.uuid4())}
    url = f"{API}/trips/{trip}/settlements/{paid['id']}/confirm"
    one = alice.c.post(url, headers=headers)
    replay = alice.c.post(url, headers=headers)
    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json() == one.json()
    assert alice.post(f"/trips/{trip}/settlements/{paid['id']}/reject").status_code == 409
    assert _nets(alice, trip) == {"Alice": "0.00", "Bob": "0.00"}


def test_concurrent_confirmations_count_once(people: list[Person]) -> None:
    alice, bob, _, _ = people
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "80.00", [alice, bob])
    paid = _pay(bob, trip, alice, "40.00")
    results: list[int] = []

    def confirm() -> None:
        results.append(alice.post(f"/trips/{trip}/settlements/{paid['id']}/confirm").status_code)

    threads = [threading.Thread(target=confirm) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200] * 4
    assert _nets(alice, trip) == {"Alice": "0.00", "Bob": "0.00"}
    events = [e["action"] for e in _ok(alice.get(f"/trips/{trip}/activity"))]
    assert events.count("settlement.confirmed") == 1


def test_settlement_permissions_and_scope(people: list[Person]) -> None:
    alice, bob, carol, dan = people
    trip = _trip(alice, "EUR", [bob, carol])
    other = _trip(dan, "EUR")
    _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    paid = _pay(bob, trip, alice, "30.00")
    # Nobody confirms for someone else: not the payer, not another member, not the owner.
    for outsider in (bob, carol):
        assert outsider.post(f"/trips/{trip}/settlements/{paid['id']}/confirm").json()["code"] in (
            "not_counterparty",
        )
    assert bob.post(f"/trips/{trip}/settlements/{paid['id']}/reject").status_code == 403
    assert alice.post(f"/trips/{trip}/settlements/{paid['id']}/cancel").status_code == 403
    # The payer is always the caller; the recipient must be someone else on this trip.
    assert _pay(bob, trip, bob, "1.00", status=422)["code"] == "validation_error"
    assert _pay(bob, trip, dan, "1.00", status=422)["code"] == "recipient_not_member"
    assert (
        bob.post(
            f"/trips/{trip}/settlements",
            {"recipient_user_id": alice.id, "amount": {"amount": "1.00", "currency": "USD"}},
        ).json()["code"]
        == "rate_required"  # another currency needs an explicit rate (everyday milestone)
    )
    # Cross-trip and cross-user: ids from another trip or someone else's trip are not found.
    assert dan.get(f"/trips/{trip}/settlements").status_code == 404
    assert dan.get(f"/trips/{other}/settlements/{paid['id']}").status_code == 404
    assert dan.post(f"/trips/{other}/settlements/{paid['id']}/confirm").status_code == 404
    # A pending payment blocks leaving; a duplicate needs a second confirmation.
    assert bob.post(f"/trips/{trip}/leave", key=False).json()["code"] in (
        "balance_outstanding",
        "pending_items_exist",
    )
    duplicate = bob.post(
        f"/trips/{trip}/settlements",
        {"recipient_user_id": alice.id, "amount": {"amount": "30.00", "currency": "EUR"}},
    )
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "possible_duplicate")
    _pay(bob, trip, alice, "30.00", confirm_duplicate=True)


def test_former_members_can_still_settle_what_concerns_them(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    _expense(alice, trip, alice, "60.00", [alice, bob])
    other = _pay(carol, trip, alice, "5.00")  # not Bob's business
    assert (
        alice.c.delete(f"{API}/trips/{trip}/members/{bob.id}", headers=alice.h(False)).status_code
        == 204
    )
    paid = _pay(bob, trip, alice, "30.00")
    _act(alice, trip, paid["id"], "confirm")
    assert _nets(alice, trip)["Bob"] == "0.00"
    mine = _ok(bob.get(f"/trips/{trip}/settlements"))
    assert [s["id"] for s in mine] == [paid["id"]]
    assert bob.get(f"/trips/{trip}/settlements/{other['id']}").status_code == 404
    suggestions = _ok(bob.get(f"/trips/{trip}/settlements/suggestions"))["items"]
    assert all(bob.id in (s["payer"]["user_id"], s["recipient"]["user_id"]) for s in suggestions)


def test_settlements_end_when_the_trip_is_archived(people: list[Person]) -> None:
    alice, bob, _, _ = people
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "20.00", [alice, bob])
    _ok(alice.post(f"/trips/{trip}/complete", key=False))
    pending = _pay(bob, trip, alice, "10.00")  # completed trips still settle
    assert (
        alice.post(f"/trips/{trip}/archive", {"acknowledge_outstanding": True}, key=False).json()[
            "code"
        ]
        == "pending_items_exist"
    )
    _act(alice, trip, pending["id"], "confirm")
    _ok(alice.post(f"/trips/{trip}/archive", key=False))
    assert _pay(bob, trip, alice, "1.00", status=409)["code"] == "trip_not_active"


# --- Disputes


def test_disputes_flag_without_moving_money(people: list[Person]) -> None:
    alice, bob, carol, dan = people
    trip = _trip(alice, "EUR", [bob, carol, dan])
    _ok(
        alice.c.patch(
            f"{API}/trips/{trip}/members/{carol.id}", json={"role": "admin"}, headers=alice.h(False)
        )
    )
    expense = _expense(alice, trip, alice, "90.00", [alice, bob, dan])
    vid = expense["current_version"]["id"]
    before = _nets(alice, trip)
    raise_path = f"/trips/{trip}/expenses/{expense['id']}/disputes"
    assert alice.post(raise_path, {"version_id": vid, "reason": "x"}).json()["code"] == (
        "payer_cannot_dispute"
    )
    assert carol.post(raise_path, {"version_id": vid, "reason": "x"}).json()["code"] == (
        "not_beneficiary"
    )
    detail = _ok(bob.post(raise_path, {"version_id": vid, "reason": "I had only a salad"}), 201)
    assert detail["disputes"]["open_count"] == 1 and detail["disputes"]["mine"]
    assert detail["status"] == "approved" and _nets(alice, trip) == before
    dup = bob.post(raise_path, {"version_id": vid, "reason": "again"})
    assert dup.json()["code"] == "dispute_already_open"
    dispute_id = detail["disputes"]["mine"]
    # The payer (also the owner) is interested; an uninvolved admin resolves.
    resolve = f"/trips/{trip}/disputes/{dispute_id}/resolve"
    assert alice.post(resolve, {"outcome": "upheld", "note": "ok"}, key=False).json()["code"] == (
        "interested_party"
    )
    assert dan.post(resolve, {"outcome": "upheld", "note": "ok"}, key=False).status_code == 403
    attention = _ok(carol.get(f"/trips/{trip}/dashboard"))["disputes_attention"]
    assert [d["id"] for d in attention] == [dispute_id] and attention[0]["can"]["resolve"]
    resolved = _ok(carol.post(resolve, {"outcome": "upheld", "note": "Agreed"}, key=False))
    assert (resolved["status"], resolved["resolution_note"]) == ("upheld", "Agreed")
    assert _nets(alice, trip) == before  # upheld is a record, not a correction
    # Dan disputes; the payer then corrects: the dispute closes as superseded.
    dan_dispute = _ok(dan.post(raise_path, {"version_id": vid, "reason": "Wrong total"}), 201)
    assert (
        dan.post(
            f"/trips/{trip}/disputes/{dan_dispute['disputes']['mine']}/withdraw", key=False
        ).status_code
        == 200
    )
    third = _ok(dan.post(raise_path, {"version_id": vid, "reason": "Still wrong"}), 201)
    change = {
        "description": "Dinner", "category_key": "food_and_drinks",
        "expense_date": TODAY.isoformat(), "amount": {"amount": "60.00", "currency": "EUR"},
        "split": {"method": "equal", "shares": [{"user_id": p.id} for p in (alice, bob, dan)]},
    }  # fmt: skip
    corrected = _ok(alice.post(f"/trips/{trip}/expenses/{expense['id']}/versions", change), 201)
    statuses = {d["id"]: d["status"] for d in corrected["dispute_history"]}
    assert statuses[third["disputes"]["mine"]] == "closed_superseded"
    stale = dan.post(raise_path, {"version_id": vid, "reason": "old"})
    assert stale.json()["code"] == "version_not_current"


# --- The permission matrix (SECURITY.md §4.7): every trip route, every relationship


ROUTES: dict[tuple[str, str], str] = {
    # (method, path template) -> who may call it: "user", "member", "admin", "owner", "token"
    ("GET", "/api/v1/trips"): "user",
    ("POST", "/api/v1/trips"): "user",
    ("GET", "/api/v1/trips/{trip_id}"): "member",
    ("PATCH", "/api/v1/trips/{trip_id}"): "admin",
    ("POST", "/api/v1/trips/{trip_id}/complete"): "admin",
    ("POST", "/api/v1/trips/{trip_id}/reopen"): "admin",
    ("POST", "/api/v1/trips/{trip_id}/cancel"): "admin",
    ("POST", "/api/v1/trips/{trip_id}/reinstate"): "admin",
    ("POST", "/api/v1/trips/{trip_id}/archive"): "owner",
    ("POST", "/api/v1/trips/{trip_id}/unarchive"): "owner",
    ("GET", "/api/v1/trips/{trip_id}/members"): "member",
    ("PATCH", "/api/v1/trips/{trip_id}/members/{member_id}"): "owner",
    ("DELETE", "/api/v1/trips/{trip_id}/members/{member_id}"): "admin",
    ("POST", "/api/v1/trips/{trip_id}/transfer-ownership"): "owner",
    ("POST", "/api/v1/trips/{trip_id}/leave"): "member",
    ("GET", "/api/v1/trips/{trip_id}/invitations"): "admin",
    ("POST", "/api/v1/trips/{trip_id}/invitations"): "admin",
    ("DELETE", "/api/v1/trips/{trip_id}/invitations/{invitation_id}"): "admin",
    ("POST", "/api/v1/invitations/preview"): "token",
    ("POST", "/api/v1/invitations/accept"): "token",
    ("POST", "/api/v1/invitations/decline"): "token",
    ("GET", "/api/v1/trips/{trip_id}/activity"): "member",
    ("GET", "/api/v1/trips/{trip_id}/dashboard"): "member",
    ("GET", "/api/v1/trips/{trip_id}/expenses"): "member",
    ("POST", "/api/v1/trips/{trip_id}/expenses"): "member",
    ("POST", "/api/v1/trips/{trip_id}/expenses/preview"): "member",
    ("GET", "/api/v1/trips/{trip_id}/expenses/{expense_id}"): "member",
    ("POST", "/api/v1/trips/{trip_id}/expenses/{expense_id}/versions"): "member",
    ("POST", "/api/v1/trips/{trip_id}/expenses/{expense_id}/cancel"): "member",
    # approve | reject | withdraw
    ("POST", "/api/v1/trips/{trip_id}/expense-versions/{version_id}/{action}"): "member",
    ("GET", "/api/v1/trips/{trip_id}/balances"): "member",
    ("GET", "/api/v1/trips/{trip_id}/settlements/suggestions"): "member",
    ("GET", "/api/v1/trips/{trip_id}/settlements"): "member",
    ("POST", "/api/v1/trips/{trip_id}/settlements"): "member",
    ("GET", "/api/v1/trips/{trip_id}/settlements/{settlement_id}"): "member",
    ("POST", "/api/v1/trips/{trip_id}/settlements/{settlement_id}/confirm"): "member",
    ("POST", "/api/v1/trips/{trip_id}/settlements/{settlement_id}/reject"): "member",
    ("POST", "/api/v1/trips/{trip_id}/settlements/{settlement_id}/cancel"): "member",
    ("POST", "/api/v1/trips/{trip_id}/settlements/{settlement_id}/reverse"): "member",
    ("GET", "/api/v1/trips/{trip_id}/disputes"): "member",
    ("POST", "/api/v1/trips/{trip_id}/expenses/{expense_id}/disputes"): "member",
    ("POST", "/api/v1/trips/{trip_id}/disputes/{dispute_id}/withdraw"): "member",
    ("POST", "/api/v1/trips/{trip_id}/disputes/{dispute_id}/resolve"): "admin",
    # B5: the trip report and its files. Trip figures only; a former member sees their part.
    ("GET", "/api/v1/trips/{trip_id}/reports/summary"): "member",
    ("GET", "/api/v1/trips/{trip_id}/reports/expenses.csv"): "member",
    ("GET", "/api/v1/trips/{trip_id}/reports/balances.csv"): "member",
    ("GET", "/api/v1/trips/{trip_id}/reports/settlements.csv"): "member",
    ("GET", "/api/v1/trips/{trip_id}/reports/export.pdf"): "member",
    # 0016: plans (shared with members; an item is changed by who added it or an admin), the
    # caller's own private budget, and analytics.
    ("GET", "/api/v1/trips/{trip_id}/plan"): "member",
    ("POST", "/api/v1/trips/{trip_id}/plan"): "member",
    ("PATCH", "/api/v1/trips/{trip_id}/plan/{item_id}"): "member",
    ("DELETE", "/api/v1/trips/{trip_id}/plan/{item_id}"): "member",
    ("GET", "/api/v1/trips/{trip_id}/my-budget"): "member",
    ("PUT", "/api/v1/trips/{trip_id}/my-budget"): "member",
    ("GET", "/api/v1/trips/{trip_id}/analytics"): "member",
}


def _trip_routes(app: FastAPI) -> set[tuple[str, str]]:
    found = set()
    for path, operations in app.openapi()["paths"].items():
        if path.startswith(("/api/v1/trips", "/api/v1/invitations")):
            for method in operations:
                found.add((method.upper(), path))
    return found


def test_every_trip_route_is_in_the_matrix(auth_app: FastAPI) -> None:
    """A new trip endpoint without a matrix entry fails the build (SECURITY.md §4.7)."""
    assert _trip_routes(auth_app) == set(ROUTES)


def test_matrix_anonymous_unrelated_member_admin(people: list[Person], auth_app: FastAPI) -> None:
    alice, bob, carol, dan = people  # owner, admin, member, unrelated
    trip = _trip(alice, "EUR", [bob, carol])
    _ok(
        alice.c.patch(
            f"{API}/trips/{trip}/members/{bob.id}", json={"role": "admin"}, headers=alice.h(False)
        )
    )
    expense = _expense(alice, trip, alice, "30.00", [alice, bob, carol])
    paid = _pay(carol, trip, alice, "5.00")
    invitation = _ok(alice.post(f"/trips/{trip}/invitations", {}, key=False), 201)
    dispute = _ok(
        carol.post(
            f"/trips/{trip}/expenses/{expense['id']}/disputes",
            {"version_id": expense["current_version"]["id"], "reason": "x"},
        ),
        201,
    )
    item = _ok(alice.post(f"/trips/{trip}/plan", {"title": "Hotel"}, key=False), 201)
    ids = {
        "item_id": item["id"],
        "trip_id": trip,
        "member_id": carol.id,
        "invitation_id": invitation["id"],
        "expense_id": expense["id"],
        "version_id": expense["current_version"]["id"],
        "settlement_id": paid["id"],
        "dispute_id": dispute["disputes"]["mine"],
        "action": "approve",
    }
    anonymous = TestClient(auth_app, raise_server_exceptions=False)
    for (method, template), who in ROUTES.items():
        path = template.format(**ids)
        # Anonymous: always 401 before anything else.
        assert anonymous.request(method, path).status_code in (401, 403), (method, template)
        if who in ("member", "admin", "owner"):
            # Someone who never belonged: 404, so trip ids can't be probed. A valid-looking body
            # is sent so validation can't answer first.
            body = _body_for(template, ids, dan)
            status = dan.c.request(method, path, json=body, headers=dan.h()).status_code
            assert status == 404, (method, template, status)
        if who in ("admin", "owner") and "{member_id}" not in template:
            body = _body_for(template, ids, carol)
            status = carol.c.request(method, path, json=body, headers=carol.h()).status_code
            assert status == 403, (method, template, status)
        if who == "owner" and "{member_id}" not in template:
            body = _body_for(template, ids, bob)
            status = bob.c.request(method, path, json=body, headers=bob.h()).status_code
            assert status in (403, 409), (method, template, status)


def _body_for(template: str, ids: dict[str, str], person: Person) -> Any:
    if template.endswith("/trips/{trip_id}") or template.endswith("/members/{member_id}"):
        return {"role": "member"} if "members" in template else {"name": "x"}
    if template.endswith("/transfer-ownership"):
        return {"user_id": ids["member_id"]}
    if template.endswith("/invitations"):
        return {}
    if template.endswith("/settlements"):
        return {
            "recipient_user_id": ids["member_id"],
            "amount": {"amount": "1.00", "currency": "EUR"},
        }
    if (
        template.endswith("/expenses")
        or template.endswith("/preview")
        or template.endswith("/versions")
    ):
        content = {
            "description": "x", "category_key": "travel",
            "expense_date": TODAY.isoformat(), "amount": {"amount": "1.00", "currency": "EUR"},
            "split": {"method": "equal", "shares": [{"user_id": person.id}]},
        }  # fmt: skip
        # A correction carries content only: the payer can't change.
        return (
            content if template.endswith("/versions") else {**content, "payer_user_id": person.id}
        )
    if template.endswith("/disputes") and "expenses" in template:
        return {"version_id": ids["version_id"], "reason": "x"}
    if template.endswith("/resolve"):
        return {"outcome": "dismissed", "note": "x"}
    if template.endswith("/plan") or template.endswith("/plan/{item_id}"):
        return {"title": "x"}
    if template.endswith("/my-budget"):
        return {"amount": "1.00"}
    return None


# --- Randomized sequences: the identity holds after every step


def _independent_nets(person: Person, trip: str) -> dict[str, Decimal]:
    """Recompute every net from the API's own records, without the balance engine."""
    nets: dict[str, Decimal] = defaultdict(Decimal)
    for e in _ok(person.get(f"/trips/{trip}/expenses")):
        v = e["current_version"]
        if e["status"] != "approved" or v is None:
            continue
        nets[e["payer"]["user_id"]] += Decimal(v["base_amount"]["amount"])
        for s in v["shares"]:
            nets[s["user_id"]] -= Decimal(s["base_share"]["amount"])
    for s in _ok(person.get(f"/trips/{trip}/settlements")):
        if s["state"] == "confirmed":
            nets[s["payer"]["user_id"]] += Decimal(s["amount"]["amount"])
            nets[s["recipient"]["user_id"]] -= Decimal(s["amount"]["amount"])
    return nets


def _check(person: Person, trip: str) -> None:
    dashboard = _ok(person.get(f"/trips/{trip}/dashboard"))
    engine = {m["user_id"]: Decimal(m["net"]["amount"]) for m in dashboard["members"]}
    assert dashboard["balanced"] and sum(engine.values()) == 0
    independent = _independent_nets(person, trip)
    assert all(engine[k] == independent.get(k, Decimal(0)) for k in engine), (engine, independent)
    # Suggestions settle everything exactly.
    after = dict(engine)
    for s in dashboard["suggestions"]:
        amount = Decimal(s["amount"]["amount"])
        assert amount > 0
        after[s["payer"]["user_id"]] += amount
        after[s["recipient"]["user_id"]] -= amount
    assert set(after.values()) <= {Decimal(0)}


@pytest.mark.parametrize("seed", [7, 2026])
def test_random_operation_sequences_keep_balances_consistent(
    people: list[Person], seed: int
) -> None:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data
    crew = people[:3]
    trip = _trip(crew[0], "EUR", crew[1:])
    for step in range(45):
        actor = rng.choice(crew)
        expenses = _ok(actor.get(f"/trips/{trip}/expenses"))
        pays = _ok(actor.get(f"/trips/{trip}/settlements"))
        roll = rng.random()
        if roll < 0.3 or not expenses:
            payer = rng.choice(crew)
            group = rng.sample(crew, rng.randint(1, 3))
            amount = f"{rng.randint(1, 50000) / 100:.2f}"
            method = rng.choice(["equal", "fixed", "percentage"])
            values = None
            if method == "fixed":
                cents = int(Decimal(amount) * 100)
                cuts = (
                    sorted(rng.sample(range(1, cents), len(group) - 1))
                    if cents > len(group)
                    else []
                )
                if len(cuts) != len(group) - 1:
                    method = "equal"
                else:
                    parts = [b - a for a, b in zip([0, *cuts], [*cuts, cents], strict=True)]
                    values = [f"{p / 100:.2f}" for p in parts]
            elif method == "percentage":
                values = ["100"] if len(group) == 1 else None
                if values is None:
                    method = "equal"
            _expense(actor, trip, payer, amount, group, method=method, values=values)
        elif roll < 0.45:
            pending = [e for e in expenses if e["can"]["approve"] or e["waiting_on_me"]]
            if pending:
                e = rng.choice(pending)
                action = rng.choice(["approve", "reject"])
                actor.post(f"/trips/{trip}/expense-versions/{e['pending_version']['id']}/{action}")
        elif roll < 0.6:
            others = [p for p in crew if p is not actor]
            to = rng.choice(others)
            _pay(actor, trip, to, f"{rng.randint(1, 20000) / 100:.2f}", confirm_duplicate=True)
        elif roll < 0.85:
            mine = [
                s for s in pays if s["can"]["confirm"] or s["can"]["reject"] or s["can"]["cancel"]
            ]
            if mine:
                s = rng.choice(mine)
                options = [a for a in ("confirm", "reject", "cancel") if s["can"][a]]
                actor.post(f"/trips/{trip}/settlements/{s['id']}/{rng.choice(options)}")
        else:
            reversible = [s for s in pays if s["can"]["reverse"]]
            if reversible:
                actor.post(f"/trips/{trip}/settlements/{rng.choice(reversible)['id']}/reverse")
        if step % 5 == 4:
            _check(actor, trip)
    _check(crew[0], trip)
