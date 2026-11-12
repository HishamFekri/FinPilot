"""Phase B (B1-B3): trips, members, invitations, shared expenses, versions, balances.

Several people sign in through the real Google routes against the fake Google (MOCKED, tests
only); exchange rates come from the fake providers in test_fx.
"""

import random
import uuid
from collections.abc import Iterator
from datetime import timedelta
from fractions import Fraction
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.services import allocation
from tests.auth_harness import sign_in, write_headers
from tests.google_fakes import FakeGoogle
from tests.test_fx import TODAY, FakeProviders, providers  # noqa: F401 - the fixture

API = "/api/v1"
NAMES = ("Alice", "Bob", "Carol", "Dan")


# --- Allocation (pure)


def test_worked_examples_split_exactly() -> None:
    a, b, c = (uuid.uuid4() for _ in range(3))
    expense = uuid.uuid4()
    shares = allocation.split(
        10000, 2, "equal", [allocation.ShareInput(x) for x in (a, b, c)], expense
    )
    assert sorted(s.share_minor for s in shares) == [3333, 3333, 3334]
    base = allocation.base_shares(342517, shares, expense)
    assert sum(base.values()) == 342517
    assert sorted(base.values()) == [114161, 114161, 114195]
    fixed = allocation.split(
        10000,
        2,
        "fixed",
        [allocation.ShareInput(a, "70.00"), allocation.ShareInput(b, "30.00")],
        expense,
    )
    assert [s.share_minor for s in fixed] == [7000, 3000]
    assert allocation.base_shares(342517, fixed, expense) == {a: 239762, b: 102755}
    jpy = allocation.split(1000, 0, "equal", [allocation.ShareInput(x) for x in (a, b, c)], expense)
    assert sorted(s.share_minor for s in jpy) == [333, 333, 334]
    percent = allocation.split(
        10000,
        2,
        "percentage",
        [
            allocation.ShareInput(a, "33.3333"),
            allocation.ShareInput(b, "33.3333"),
            allocation.ShareInput(c, "33.3334"),
        ],
        expense,
    )
    assert sum(s.share_minor for s in percent) == 10000


@pytest.mark.parametrize(
    ("method", "values", "code"),
    [
        ("fixed", ["70.00", "20.00"], "split_sum_mismatch"),
        ("fixed", ["70.001", "29.999"], "invalid_amount"),
        ("percentage", ["50", "49"], "percentage_sum_invalid"),
        ("percentage", ["50.00001", "49.99999"], "validation_error"),
    ],
)
def test_bad_splits_are_refused(method: str, values: list[str], code: str) -> None:
    from app.core.errors import ApiError

    inputs = [allocation.ShareInput(uuid.uuid4(), v) for v in values]
    with pytest.raises(ApiError) as error:
        allocation.split(10000, 2, method, inputs, uuid.uuid4())
    assert error.value.code == code


def test_allocation_properties_hold_for_random_cases() -> None:
    rng = random.Random(20261109)  # noqa: S311 - reproducible test data, not secrets
    for _ in range(3000):
        members = [uuid.uuid4() for _ in range(rng.randint(1, 8))]
        weights: dict[uuid.UUID, Fraction | int] = {m: rng.randint(0, 50) for m in members}
        if not any(weights.values()):
            weights[members[0]] = 1
        total = rng.randint(0, 10**9)
        expense = uuid.uuid4()
        out = allocation.allocate(total, weights, expense)
        assert sum(out.values()) == total
        assert all(v >= 0 for v in out.values())
        assert all(out[m] == 0 for m, w in weights.items() if w == 0)
        assert out == allocation.allocate(total, dict(reversed(list(weights.items()))), expense)


def test_suggestions_settle_everything_but_arent_always_minimal() -> None:
    c1, c2, d1, d2, d3, d4 = (uuid.UUID(int=i) for i in range(1, 7))
    nets = {c1: 9, c2: 6, d1: -5, d2: -4, d3: -3, d4: -3}
    payments = allocation.suggest(nets)
    assert len(payments) == 5  # the documented counterexample; the best is 4
    after = dict(nets)
    for debtor, creditor, amount in payments:
        after[debtor] += amount
        after[creditor] -= amount
    assert set(after.values()) == {0}
    assert payments == allocation.suggest(dict(reversed(list(nets.items()))))


# --- API


class Person:
    def __init__(self, client: TestClient, csrf: str, name: str) -> None:
        self.c, self.csrf, self.name = client, csrf, name
        self.id = client.get(f"{API}/auth/me").json()["id"]

    def h(self, key: bool = True) -> dict[str, str]:
        headers = write_headers(self.csrf)
        if key:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        return headers

    def post(self, path: str, body: Any = None, key: bool = True) -> Any:
        return self.c.post(f"{API}{path}", json=body, headers=self.h(key))

    def get(self, path: str, **params: Any) -> Any:
        return self.c.get(f"{API}{path}", params=params)


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def people(
    auth_app: FastAPI,
    google: FakeGoogle,
    providers: FakeProviders,
) -> Iterator[list[Person]]:
    out = []
    for i, name in enumerate(NAMES):
        client = TestClient(auth_app, raise_server_exceptions=False)
        client.__enter__()
        csrf = sign_in(
            client, google, subject=f"google-{name}", email=f"{name.lower()}@example.com", name=name
        )
        out.append(Person(client, csrf, name))
        assert i == len(out) - 1
    yield out
    for person in out:
        person.c.__exit__(None, None, None)


def _trip(owner: Person, currency: str = "EUR", members: list[Person] | None = None) -> str:
    trip = _ok(owner.post("/trips", {"name": "Lisbon", "base_currency": currency}), 201)
    for person in members or []:
        token = _ok(
            owner.post(f"/trips/{trip['id']}/invitations", {"label": person.name}, key=False), 201
        )["token"]
        _ok(person.post("/invitations/accept", {"token": token}, key=False))
    return str(trip["id"])


def _expense(
    by: Person,
    trip: str,
    payer: Person,
    amount: str,
    beneficiaries: list[Person],
    *,
    currency: str = "EUR",
    method: str = "equal",
    values: list[str] | None = None,
    rate: dict[str, Any] | None = None,
    description: str = "Dinner",
    status: int = 201,
) -> Any:
    body: dict[str, Any] = {
        "payer_user_id": payer.id,
        "description": description,
        "category_key": "food_and_drinks",
        "expense_date": TODAY.isoformat(),
        "amount": {"amount": amount, "currency": currency},
        "split": {
            "method": method,
            "shares": [
                {"user_id": p.id, **({"value": values[i]} if values else {})}
                for i, p in enumerate(beneficiaries)
            ],
        },
    }
    if rate:
        body["rate"] = rate
    return _ok(by.post(f"/trips/{trip}/expenses", body), status)


def _nets(person: Person, trip: str) -> dict[str, str]:
    body = _ok(person.get(f"/trips/{trip}/dashboard"))
    assert body["balanced"] is True
    return {m["display_name"]: m["net"]["amount"] for m in body["members"]}


def test_invitations_join_once_and_members_show_no_emails(people: list[Person]) -> None:
    alice, bob, carol, dan = people
    trip = _ok(alice.post("/trips", {"name": "Lisbon", "base_currency": "EUR"}), 201)
    assert (trip["role"], trip["can"]["owner"]) == ("owner", True)
    link = _ok(alice.post(f"/trips/{trip['id']}/invitations", {"label": "Bob"}, key=False), 201)
    preview = _ok(bob.post("/invitations/preview", {"token": link["token"]}, key=False))
    assert (preview["trip_name"], preview["inviter"], preview["already_member"]) == (
        "Lisbon", "Alice", False,
    )  # fmt: skip
    _ok(bob.post("/invitations/accept", {"token": link["token"]}, key=False))
    reused = carol.post("/invitations/accept", {"token": link["token"]}, key=False)
    assert (reused.status_code, reused.json()["code"]) == (410, "invitation_used")
    bad = carol.post("/invitations/accept", {"token": "x" * 43}, key=False)
    assert bad.status_code == 404
    members = _ok(bob.get(f"/trips/{trip['id']}/members"))
    assert {m["display_name"] for m in members} == {"Alice", "Bob"}
    assert all(set(m) == {"user_id", "display_name", "role", "status"} for m in members)
    # Someone who never belonged can't even see that the trip exists.
    assert dan.get(f"/trips/{trip['id']}").status_code == 404
    assert dan.get(f"/trips/{trip['id']}/expenses").status_code == 404
    # Only admins invite.
    assert bob.post(f"/trips/{trip['id']}/invitations", {}, key=False).status_code == 403
    # An expired link is refused clearly.
    revoked = _ok(alice.post(f"/trips/{trip['id']}/invitations", {}, key=False), 201)
    assert (
        alice.c.delete(
            f"{API}/trips/{trip['id']}/invitations/{revoked['id']}", headers=alice.h(False)
        ).status_code
        == 204
    )
    assert (
        carol.post("/invitations/accept", {"token": revoked["token"]}, key=False).status_code == 410
    )


def test_worked_scenario_and_corrections_after_approval(people: list[Person]) -> None:
    """FINANCIAL_RULES.md §9.3: EUR trip, members A, B, C."""
    a, b, c, _ = people
    trip = _trip(a, "EUR", [b, c])
    x1 = _expense(a, trip, a, "300.00", [a, b, c])
    assert x1["status"] == "approved" and x1["classification"] == "shared"
    hotel = _expense(
        b,
        trip,
        b,
        "3000.00",
        [a, c],
        currency="TRY",
        rate={"manual": "0.0200"},
        description="Hotel",
    )
    assert hotel["current_version"]["base_amount"] == {"amount": "60.00", "currency": "EUR"}
    assert hotel["current_version"]["rate"]["is_manual"] is True
    assert hotel["classification"] == "paid_for_others"
    _expense(c, trip, c, "100.00", [a], description="Museum")
    assert _nets(a, trip) == {"Alice": "70.00", "Bob": "-40.00", "Carol": "-30.00"}
    suggestions = _ok(a.get(f"/trips/{trip}/dashboard"))["suggestions"]
    pairs = [
        (s["payer"]["display_name"], s["recipient"]["display_name"], s["amount"]["amount"])
        for s in suggestions
    ]
    assert pairs == [("Bob", "Alice", "40.00"), ("Carol", "Alice", "30.00")]
    # X1 corrected up to 330 by its payer: approved at once.
    corrected = _ok(
        a.post(
            f"/trips/{trip}/expenses/{x1['id']}/versions",
            {
                "description": "Dinner",
                "category_key": "food_and_drinks",
                "expense_date": TODAY.isoformat(),
                "amount": {"amount": "330.00", "currency": "EUR"},
                "split": {"method": "equal", "shares": [{"user_id": p.id} for p in (a, b, c)]},
            },
        ),
        201,
    )
    assert corrected["is_corrected"] and len(corrected["history"]) == 2
    assert [v["state"] for v in corrected["history"]] == ["superseded", "approved"]
    assert _nets(a, trip) == {"Alice": "90.00", "Bob": "-50.00", "Carol": "-40.00"}
    dashboard = _ok(a.get(f"/trips/{trip}/dashboard"))
    assert dashboard["total"]["amount"] == "490.00"  # 330 + 60 + 100
    assert (dashboard["shared"]["amount"], dashboard["personal"]["amount"]) == ("490.00", "0.00")


def test_only_the_payer_confirms_and_proposals_never_count(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    # Carol is an admin; still no override.
    _ok(
        alice.c.patch(
            f"{API}/trips/{trip}/members/{carol.id}", json={"role": "admin"}, headers=alice.h(False)
        )
    )
    proposed = _expense(alice, trip, bob, "90.00", [alice, bob, carol])
    assert proposed["status"] == "pending_payer" and proposed["current_version"] is None
    assert _nets(alice, trip) == {"Alice": "0.00", "Bob": "0.00", "Carol": "0.00"}
    version = proposed["pending_version"]["id"]
    for outsider in (alice, carol):
        refused = outsider.post(f"/trips/{trip}/expense-versions/{version}/approve")
        assert (refused.status_code, refused.json()["code"]) == (403, "not_payer")
    listed = _ok(bob.get(f"/trips/{trip}/expenses"))[0]
    assert listed["waiting_on_me"] and listed["can"]["approve"]
    assert _ok(bob.get(f"/trips/{trip}/dashboard"))["waiting_on_me"][0]["version_id"] == version
    # A trip can't be completed while something waits.
    assert alice.post(f"/trips/{trip}/complete", key=False).json()["code"] == "pending_items_exist"
    approved = _ok(bob.post(f"/trips/{trip}/expense-versions/{version}/approve"))
    assert approved["status"] == "approved"
    assert _nets(alice, trip) == {"Alice": "-30.00", "Bob": "60.00", "Carol": "-30.00"}
    again = bob.post(f"/trips/{trip}/expense-versions/{version}/approve")
    assert again.json()["code"] == "version_not_pending"

    # A correction by someone else waits; the approved version keeps counting.
    change = {
        "description": "Dinner",
        "category_key": "food_and_drinks",
        "expense_date": TODAY.isoformat(),
        "amount": {"amount": "120.00", "currency": "EUR"},
        "split": {"method": "equal", "shares": [{"user_id": p.id} for p in (alice, bob, carol)]},
    }
    pending = _ok(alice.post(f"/trips/{trip}/expenses/{proposed['id']}/versions", change), 201)
    assert pending["has_pending_correction"]
    assert _nets(alice, trip)["Bob"] == "60.00"
    second = alice.post(f"/trips/{trip}/expenses/{proposed['id']}/versions", change)
    assert (second.status_code, second.json()["code"]) == (409, "correction_already_pending")
    rejected = _ok(
        bob.post(
            f"/trips/{trip}/expense-versions/{pending['pending_version']['id']}/reject",
            {"reason": "It was 90"},
        )
    )
    assert rejected["history"][-1]["state"] == "rejected"
    assert rejected["history"][-1]["decision_reason"] == "It was 90"
    assert _nets(alice, trip)["Bob"] == "60.00"
    # A cancellation by the payer is immediate and closes the expense.
    cancelled = _ok(bob.post(f"/trips/{trip}/expenses/{proposed['id']}/cancel"), 201)
    assert cancelled["status"] == "cancelled"
    assert _nets(alice, trip) == {"Alice": "0.00", "Bob": "0.00", "Carol": "0.00"}
    closed = bob.post(f"/trips/{trip}/expenses/{proposed['id']}/versions", change)
    assert closed.json()["code"] == "expense_closed"


def test_withdrawn_proposal_and_member_permissions(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    proposed = _expense(bob, trip, carol, "30.00", [bob, carol])
    vid = proposed["pending_version"]["id"]
    # Only the proposer or an admin can withdraw.
    assert carol.post(f"/trips/{trip}/expense-versions/{vid}/withdraw").status_code == 403
    withdrawn = _ok(bob.post(f"/trips/{trip}/expense-versions/{vid}/withdraw"))
    assert withdrawn["status"] == "cancelled"
    # A plain member can't correct someone else's expense.
    mine = _expense(carol, trip, carol, "20.00", [bob, carol])
    change = {
        "description": "x", "category_key": "travel", "expense_date": TODAY.isoformat(),
        "amount": {"amount": "10.00", "currency": "EUR"},
        "split": {"method": "equal", "shares": [{"user_id": carol.id}]},
    }  # fmt: skip
    assert bob.post(f"/trips/{trip}/expenses/{mine['id']}/versions", change).status_code == 403
    # Payer and beneficiaries must be current members of this trip.
    refused = _expense(alice, trip, alice, "10.00", [alice, people[3]], status=422)
    assert refused["code"] == "beneficiary_not_active_member"


def test_former_members_keep_a_scoped_view(people: list[Person]) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    shared = _expense(alice, trip, alice, "60.00", [alice, bob])
    private = _expense(alice, trip, alice, "40.00", [alice, carol], description="Carol's tour")
    # Bob owes, so he can't leave.
    assert bob.post(f"/trips/{trip}/leave", key=False).json()["code"] == "balance_outstanding"
    # Removing him changes no balance.
    before = _nets(alice, trip)
    assert (
        alice.c.delete(f"{API}/trips/{trip}/members/{bob.id}", headers=alice.h(False)).status_code
        == 204
    )
    assert _nets(alice, trip) == before
    # He still sees what involves him, and his own balance; nothing else.
    listed = _ok(bob.get(f"/trips/{trip}/expenses"))
    assert [e["id"] for e in listed] == [shared["id"]]
    assert bob.get(f"/trips/{trip}/expenses/{private['id']}").status_code == 404
    mine = _ok(bob.get(f"/trips/{trip}/dashboard"))["members"]
    assert [m["display_name"] for m in mine] == ["Bob"] and mine[0]["net"]["amount"] == "-30.00"
    assert bob.get(f"/trips/{trip}/activity").status_code == 403
    assert _expense(bob, trip, bob, "5.00", [bob], status=403)["code"] == "forbidden"
    # New expenses can't include him; a rejoin restores everything.
    assert (
        _expense(alice, trip, alice, "10.00", [bob], status=422)["code"]
        == "beneficiary_not_active_member"
    )
    token = _ok(alice.post(f"/trips/{trip}/invitations", {}, key=False), 201)["token"]
    _ok(bob.post("/invitations/accept", {"token": token}, key=False))
    assert len(_ok(bob.get(f"/trips/{trip}/expenses"))) == 2


def test_lifecycle_base_currency_lock_and_archive(people: list[Person]) -> None:
    alice, bob, _, _ = people
    trip = _trip(alice, "EUR", [bob])
    detail = alice.get(f"/trips/{trip}")
    version = detail.headers["ETag"]
    changed = alice.c.patch(
        f"{API}/trips/{trip}",
        json={"base_currency": "USD"},
        headers={**alice.h(False), "If-Match": version},
    )
    assert changed.status_code == 200 and changed.json()["base_currency"] == "USD"
    _expense(alice, trip, alice, "50.00", [alice, bob], currency="USD")
    locked = alice.c.patch(
        f"{API}/trips/{trip}",
        json={"base_currency": "EUR"},
        headers={**alice.h(False), "If-Match": changed.headers["ETag"]},
    )
    assert locked.json()["code"] == "base_currency_locked"
    assert bob.post(f"/trips/{trip}/complete", key=False).status_code == 403
    assert _ok(alice.post(f"/trips/{trip}/complete", key=False))["status"] == "completed"
    # Completed: no new expenses.
    assert (
        _expense(alice, trip, alice, "5.00", [alice], currency="USD", status=409)["code"]
        == "trip_not_active"
    )
    outstanding = alice.post(f"/trips/{trip}/archive", key=False)
    assert outstanding.json()["code"] == "balances_outstanding"
    archived = _ok(
        alice.post(f"/trips/{trip}/archive", {"acknowledge_outstanding": True}, key=False)
    )
    assert archived["status"] == "archived"
    activity = [e["action"] for e in _ok(alice.get(f"/trips/{trip}/activity"))]
    assert {
        "trip.created",
        "trip.member.joined",
        "expense.created",
        "trip.completed",
        "trip.archived",
    } <= set(activity)


def test_reference_rate_converts_once_and_splits_exactly(
    people: list[Person],
    providers: FakeProviders,
) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    rate_id = _ok(alice.get("/fx/convert", **{"from": "USD", "to": "EUR"}))["rate"]["id"]
    expense = _expense(
        alice, trip, alice, "100.00", [alice, bob, carol], currency="USD", rate={"rate_id": rate_id}
    )
    version = expense["current_version"]
    assert version["amount"] == {"amount": "100.00", "currency": "USD"}
    assert version["base_amount"] == {"amount": "89.08", "currency": "EUR"}  # 100 x 0.89075
    assert version["rate"]["rate"] == "0.89075" and not version["rate"]["is_manual"]
    assert sorted(s["share"]["amount"] for s in version["shares"]) == ["33.33", "33.33", "33.34"]
    assert sum(int(s["base_share"]["amount"].replace(".", "")) for s in version["shares"]) == 8908
    # The market moving doesn't touch it.
    providers.rates[("USD", "EUR")] = "0.95"
    again = _ok(alice.get(f"/trips/{trip}/expenses/{expense['id']}"))
    assert again["current_version"]["base_amount"]["amount"] == "89.08"
    missing = _expense(alice, trip, alice, "10.00", [alice], currency="USD", status=422)
    assert missing["code"] == "rate_required"


def test_preview_writes_nothing(people: list[Person], factory: "sessionmaker[Session]") -> None:
    alice, bob, _, _ = people
    trip = _trip(alice, "EUR", [bob])
    with factory() as db:
        before = db.execute(text("SELECT count(*) FROM trip_expenses")).scalar()
    body = {
        "payer_user_id": alice.id, "description": "Taxi", "category_key": "transportation",
        "expense_date": TODAY.isoformat(), "amount": {"amount": "10.00", "currency": "EUR"},
        "split": {"method": "percentage", "shares": [
            {"user_id": alice.id, "value": "60"}, {"user_id": bob.id, "value": "40"},
        ]},
    }  # fmt: skip
    preview = _ok(alice.post(f"/trips/{trip}/expenses/preview", body, key=False))
    assert [s["share"]["amount"] for s in preview["shares"]] == ["6.00", "4.00"]
    with factory() as db:
        assert db.execute(text("SELECT count(*) FROM trip_expenses")).scalar() == before


def test_trip_responses_reveal_nothing_private(people: list[Person]) -> None:
    """SECURITY.md §4.5: marker strings in private data never appear in trip responses."""
    alice, bob, _, _ = people
    marker = "PRIVATE-MARKER-7f3a"
    _ok(alice.post("/setup", {"currency": "EUR"}, key=False))
    _ok(
        alice.post(
            "/accounts",
            {"name": f"{marker} savings", "type": "savings", "currency": "EUR",
             "opening_balance": {"amount": "1000.00", "currency": "EUR"}},
        ),
        201,
    )  # fmt: skip
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "20.00", [alice, bob])
    for path in ("", "/members", "/expenses", "/dashboard", "/activity"):
        text_body = bob.get(f"/trips/{trip}{path}").text
        assert marker not in text_body
        assert "alice@example.com" not in text_body and "1000.00" not in text_body


def test_database_refuses_bad_versions_and_shares(
    people: list[Person], factory: "sessionmaker[Session]"
) -> None:
    alice, bob, _, _ = people
    trip = _trip(alice, "EUR", [bob])
    expense = _expense(alice, trip, alice, "10.00", [alice, bob])
    vid = expense["current_version"]["id"]
    with factory() as db, pytest.raises(DBAPIError, match="immutable"):
        db.execute(
            text("UPDATE trip_expense_versions SET amount_minor = 1 WHERE id = :v"), {"v": vid}
        )
    with factory() as db, pytest.raises(DBAPIError, match="can't go from"):
        db.execute(
            text(
                "UPDATE trip_expense_versions SET state = 'pending', decided_at = NULL"
                " WHERE id = :v"
            ),
            {"v": vid},
        )
    with factory() as db, pytest.raises(DBAPIError, match="immutable"):
        db.execute(
            text("UPDATE expense_shares SET share_minor = 1 WHERE version_id = :v"), {"v": vid}
        )
    with factory() as db, pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("DELETE FROM trip_expenses WHERE id = :e"), {"e": expense["id"]})
    # Shares must add up, checked at commit.
    with factory() as db, pytest.raises(DBAPIError, match="sum"):
        new = db.execute(
            text(
                "INSERT INTO trip_expenses (trip_id, payer_user_id, created_by) "
                "VALUES (:t, :u, :u) RETURNING id"
            ),
            {"t": trip, "u": alice.id},
        ).scalar()
        version = db.execute(
            text(
                "INSERT INTO trip_expense_versions (expense_id, trip_id, version_no, kind, state,"
                " proposed_by, description, category_key, expense_date, amount_minor, currency,"
                " base_currency, base_amount_minor, split_method, decided_at)"
                " VALUES (:e, :t, 1, 'initial', 'approved', :u, 'x', 'travel', CURRENT_DATE, 1000,"
                " 'EUR', 'EUR', 1000, 'equal', now()) RETURNING id"
            ),
            {"e": new, "t": trip, "u": alice.id},
        ).scalar()
        db.execute(
            text(
                "INSERT INTO expense_shares"
                " (version_id, user_id, trip_id, share_minor, base_share_minor)"
                " VALUES (:v, :u, :t, 999, 999)"
            ),
            {"v": version, "u": alice.id, "t": trip},
        )
        db.commit()


def test_dates_and_text_limits(people: list[Person]) -> None:
    alice, _, _, _ = people
    bad = alice.post(
        "/trips",
        {"name": "x", "base_currency": "EUR", "start_date": TODAY.isoformat(),
         "end_date": (TODAY - timedelta(days=1)).isoformat()},
    )  # fmt: skip
    assert bad.status_code == 422
    assert alice.post("/trips", {"name": " ", "base_currency": "EUR"}).status_code == 422
    assert alice.post("/trips", {"name": "x", "base_currency": "ZZZ"}).status_code == 422
