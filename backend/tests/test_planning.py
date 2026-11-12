"""Trip scheduling and planning, shared and private trip budgets, trip analytics, budget removal,
and favourite categories (FINANCIAL_RULES.md §6, §11.12)."""

from datetime import timedelta
from typing import Any

from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_links import _category_id, _setup
from tests.test_trips import API, Person, _expense, _ok, _trip, people  # noqa: F401 - fixtures


def _patch(person: Person, path: str, body: Any, version: int) -> Any:
    return person.c.patch(f"{API}{path}", json=body,
                          headers={**person.h(False), "If-Match": f'"{version}"'})  # fmt: skip


def _trip_state(person: Person, trip: str) -> dict[str, Any]:
    return dict(_ok(person.get(f"/trips/{trip}")))


# --- Scheduling


def test_phases_follow_dates_and_dates_are_validated(people: list[Person]) -> None:
    alice, *_ = people
    later = TODAY + timedelta(days=30)
    trip = _ok(alice.post("/trips", {
        "name": "Porto", "base_currency": "EUR", "destination": "Porto",
        "start_date": later.isoformat(), "end_date": (later + timedelta(days=4)).isoformat(),
        "description": "Long weekend",
    }), 201)  # fmt: skip
    assert trip["phase"] == "upcoming"
    # Postponed into the past and present: ongoing, then ended.
    now = _patch(alice, f"/trips/{trip['id']}", {"start_date": TODAY.isoformat()}, trip["version"])
    assert _ok(now)["phase"] == "ongoing"
    t = _trip_state(alice, trip["id"])
    past = _patch(alice, f"/trips/{trip['id']}", {
        "start_date": (TODAY - timedelta(days=9)).isoformat(),
        "end_date": (TODAY - timedelta(days=2)).isoformat(),
    }, t["version"])  # fmt: skip
    assert _ok(past)["phase"] == "ended"
    bad = alice.post("/trips", {"name": "X", "base_currency": "EUR",
                                "start_date": later.isoformat(),
                                "end_date": TODAY.isoformat()})  # fmt: skip
    assert bad.status_code == 422


def test_cancelling_keeps_history_blocks_new_expenses_and_can_be_undone(
    people: list[Person],
) -> None:
    alice, bob, *_ = people
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "60.00", [alice, bob])
    # Only admins cancel.
    assert bob.post(f"/trips/{trip}/cancel", key=False).status_code == 403
    cancelled = _ok(alice.post(f"/trips/{trip}/cancel", key=False))
    assert (cancelled["status"], cancelled["phase"]) == ("cancelled", "cancelled")
    assert cancelled["cancelled_at"] is not None
    assert cancelled["can"]["add_expense"] is False
    refused = _expense(alice, trip, alice, "10.00", [alice], status=409)
    assert refused["code"]
    # The expense and the balance are still there.
    board = _ok(alice.get(f"/trips/{trip}/dashboard"))
    assert board["total"]["amount"] == "60.00"
    # Archived and back: it returns to cancelled, not completed.
    _ok(alice.post(f"/trips/{trip}/archive", {"acknowledge_outstanding": True}, key=False))
    back = _ok(alice.post(f"/trips/{trip}/unarchive", key=False))
    assert back["status"] == "cancelled"
    reinstated = _ok(alice.post(f"/trips/{trip}/reinstate", key=False))
    assert (reinstated["status"], reinstated["cancelled_at"]) == ("active", None)
    activity = [e["action"] for e in _ok(alice.get(f"/trips/{trip}/activity"))]
    assert "trip.cancelled" in activity and "trip.reinstated" in activity


def test_leaving_an_upcoming_trip_is_allowed_only_without_obligations(
    people: list[Person],
) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    later = TODAY + timedelta(days=20)
    t = _trip_state(alice, trip)
    _ok(_patch(alice, f"/trips/{trip}", {"start_date": later.isoformat()}, t["version"]))
    # Carol has nothing outstanding: she can leave before it starts.
    assert carol.post(f"/trips/{trip}/leave", key=False).status_code == 204
    # Bob owes Alice for a deposit: he must settle first, and nothing is erased.
    _expense(alice, trip, alice, "100.00", [alice, bob])
    owed = bob.post(f"/trips/{trip}/leave", key=False)
    assert (owed.status_code, owed.json()["code"]) == (409, "balance_outstanding")
    assert _ok(alice.get(f"/trips/{trip}/dashboard"))["total"]["amount"] == "100.00"


# --- Planning and budgets


def test_plan_items_are_shared_estimates_that_never_count_as_spending(
    people: list[Person],
) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob])
    hotel = _ok(alice.post(f"/trips/{trip}/plan", {
        "kind": "accommodation", "title": "Hotel Ribeira",
        "starts_on": TODAY.isoformat(), "ends_on": (TODAY + timedelta(days=3)).isoformat(),
        "booking_status": "booked", "estimated": "450.00", "deposit": "100.00",
        "reference": "ABC123", "url": "https://example.com/booking",
    }, key=False), 201)  # fmt: skip
    _ok(bob.post(f"/trips/{trip}/plan", {"kind": "transport", "title": "Flight",
                                          "transport_mode": "flight",
                                          "estimated": "300.00"}, key=False), 201)  # fmt: skip
    bad_url = alice.post(f"/trips/{trip}/plan", {"title": "x", "url": "http://x.example"},
                         key=False)  # fmt: skip
    assert bad_url.json()["code"] == "url_not_https"
    backwards = {"title": "x", "starts_on": TODAY.isoformat(),
                 "ends_on": (TODAY - timedelta(days=1)).isoformat()}  # fmt: skip
    bad_dates = alice.post(f"/trips/{trip}/plan", backwards, key=False)
    assert bad_dates.status_code == 422
    items = _ok(bob.get(f"/trips/{trip}/plan"))
    assert {i["title"] for i in items} == {"Hotel Ribeira", "Flight"}
    # Bob can't edit Alice's item; a non-member sees nothing.
    assert next(i for i in items if i["title"] == "Hotel Ribeira")["can_edit"] is False
    denied = _patch(bob, f"/trips/{trip}/plan/{hotel['id']}", {"title": "Mine"}, hotel["version"])
    assert denied.status_code == 403
    assert carol.get(f"/trips/{trip}/plan").status_code == 404
    # Estimates aren't spending: the trip has spent nothing.
    stats = _ok(alice.get(f"/trips/{trip}/analytics"))
    assert (stats["planned"]["amount"], stats["actual"]["amount"]) == ("750.00", "0.00")
    assert stats["deposits"]["amount"] == "100.00"
    # The real cost, once approved, is what counts; linked to its plan item it's accommodation.
    paid = _expense(alice, trip, alice, "460.00", [alice, bob], description="Hotel")
    _ok(_patch(alice, f"/trips/{trip}/plan/{hotel['id']}", {"expense_id": paid["id"]},
               hotel["version"]))  # fmt: skip
    stats = _ok(alice.get(f"/trips/{trip}/analytics"))
    assert stats["actual"]["amount"] == "460.00"
    kinds = {k["key"]: k["amount"]["amount"] for k in stats["actual_by_kind"]}
    assert kinds == {"accommodation": "460.00"}
    assert stats["cost_per_member"]["amount"] == "230.00"


def test_shared_budget_is_for_the_group_and_admins_set_it(people: list[Person]) -> None:
    alice, bob, *_ = people
    trip = _trip(alice, "EUR", [bob])
    t = _trip_state(alice, trip)
    assert (
        _patch(bob, f"/trips/{trip}", {"shared_budget": "2000.00"}, t["version"]).status_code == 403
    )
    updated = _ok(_patch(alice, f"/trips/{trip}", {"shared_budget": "2000.00"}, t["version"]))
    assert updated["shared_budget"] == {"amount": "2000.00", "currency": "EUR"}
    _expense(alice, trip, alice, "500.00", [alice, bob])
    stats = _ok(bob.get(f"/trips/{trip}/analytics"))
    assert (stats["shared_remaining"]["amount"], stats["shared_percent"]) == ("1500.00", "25.0")
    # Changing the base currency with a budget set asks for the budget again.
    fresh = _trip(alice, "EUR")
    f = _trip_state(alice, fresh)
    f = _ok(_patch(alice, f"/trips/{fresh}", {"shared_budget": "100.00"}, f["version"]))
    clash = _patch(alice, f"/trips/{fresh}", {"base_currency": "USD"}, f["version"])
    assert clash.json()["code"] == "shared_budget_currency"


def test_a_personal_trip_budget_is_private_and_uses_the_share_not_the_cash(
    people: list[Person],
) -> None:
    alice, bob, *_ = people
    trip = _trip(alice, "EUR", [bob])
    _ok(bob.c.put(f"{API}/trips/{trip}/my-budget", json={"amount": "500.00"},
                  headers=bob.h(False)))  # fmt: skip
    # Alice paid 300 for both: her share is 150; Bob's is 150 though he paid nothing.
    _expense(alice, trip, alice, "300.00", [alice, bob])
    bob_stats = _ok(bob.get(f"/trips/{trip}/analytics"))
    assert (bob_stats["my_share"]["amount"], bob_stats["my_paid"]["amount"]) == ("150.00", "0.00")
    assert (bob_stats["my_remaining"]["amount"], bob_stats["my_percent"]) == ("350.00", "30.0")
    # Nobody else can see Bob's budget: not through the budget route, analytics or the dashboard.
    assert _ok(alice.get(f"/trips/{trip}/my-budget"))["amount"] is None
    alice_stats = _ok(alice.get(f"/trips/{trip}/analytics"))
    assert alice_stats["my_budget"] is None
    for path in (f"/trips/{trip}", f"/trips/{trip}/dashboard", f"/trips/{trip}/analytics",
                 f"/trips/{trip}/members"):  # fmt: skip
        assert "500.00" not in alice.get(path).text
    # Removing it.
    cleared = _ok(bob.c.put(f"{API}/trips/{trip}/my-budget", json={"amount": None},
                            headers=bob.h(False)))  # fmt: skip
    assert cleared["amount"] is None


# --- Budgets: restore and safe removal


def _budget(person: Person, body: dict[str, Any]) -> Any:
    return _ok(person.c.put(f"{API}/budgets", json=body, headers=person.h(False)))


def test_a_new_budget_can_be_deleted_but_one_with_history_is_only_ended_and_restored(
    people: list[Person],
) -> None:
    alice, bob, *_ = people
    _setup(alice)
    month = TODAY.replace(day=1)
    groceries = _category_id(alice, "groceries")
    _budget(alice, {"category_id": groceries, "limit": {"amount": "300.00", "currency": "EUR"},
                    "from_month": month.strftime("%Y-%m")})  # fmt: skip
    # Bob can't remove Alice's budget (it isn't his: nothing to delete).
    bob_try = bob.post("/budgets/delete", {"category_id": groceries, "currency": "EUR"}, key=False)
    assert bob_try.status_code in (404, 422)
    gone = _ok(alice.post("/budgets/delete", {"category_id": groceries, "currency": "EUR"},
                          key=False))  # fmt: skip
    assert gone["budgets"] == []

    last_month = (month - timedelta(days=1)).replace(day=1)
    _budget(alice, {"category_id": groceries, "limit": {"amount": "250.00", "currency": "EUR"},
                    "from_month": last_month.strftime("%Y-%m")})  # fmt: skip
    kept = alice.post("/budgets/delete", {"category_id": groceries, "currency": "EUR"}, key=False)
    assert (kept.status_code, kept.json()["code"]) == (409, "budget_has_history")
    series = {"category_id": groceries, "currency": "EUR", "from_month": month.strftime("%Y-%m")}
    ended = _ok(alice.post("/budgets/end", series, key=False))
    assert ended["budgets"] == [] and ended["archived"][0]["last_limit"]["amount"] == "250.00"
    # Last month still shows it.
    before = _ok(alice.get("/budgets", month=last_month.strftime("%Y-%m")))
    assert before["budgets"][0]["limit"]["amount"] == "250.00"
    restored = _ok(alice.post("/budgets/restore", {"category_id": groceries, "currency": "EUR",
                                                   "from_month": month.strftime("%Y-%m")},
                              key=False))  # fmt: skip
    assert restored["budgets"][0]["limit"]["amount"] == "250.00"


def test_favourite_categories(people: list[Person]) -> None:
    alice, bob, *_ = people
    _setup(alice)
    _setup(bob)
    food = _category_id(alice, "food_and_drinks")
    pinned = _ok(alice.c.put(f"{API}/categories/{food}/favourite", json={"favourite": True},
                             headers=alice.h(False)))  # fmt: skip
    assert pinned["is_favourite"] is True
    assert next(c for c in _ok(alice.get("/categories")) if c["id"] == food)["is_favourite"]
    assert bob.c.put(f"{API}/categories/{food}/favourite", json={"favourite": True},
                     headers=bob.h(False)).status_code == 404  # fmt: skip
    # Everyone gets "Interest & fees", including people who set up before it existed.
    assert any(c["system_key"] == "interest_fees" for c in _ok(bob.get("/categories")))
