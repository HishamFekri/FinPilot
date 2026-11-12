"""Everyday features: refunds, quick templates, savings goals, category groups, the calendar,
subscriptions, cross-currency trip settlements, and one-step personal linking.

People sign in through the real Google routes against the fake Google, and rates come from the
fake providers (MOCKED, tests only).
"""

import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_links import _balance, _category_id, _link, _report, _setup, _summary
from tests.test_settlements import _act
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
LAST_MONTH_DAY = MONTH - timedelta(days=3)


def _spend(person: Person, account: dict[str, Any], amount: str, category: str = "groceries",
           when: Any = None, **extra: Any) -> Any:  # fmt: skip
    body = {
        "type": "expense",
        "account_id": account["id"],
        "amount": {"amount": amount, "currency": account["currency"]},
        "category_id": _category_id(person, category),
        **({"effective_date": when.isoformat()} if when else {}),
        **extra,
    }
    return _ok(person.post("/transactions", body), 201)


def _refund(person: Person, txn: str, amount: str, currency: str = "EUR", status: int = 201,
            **extra: Any) -> Any:  # fmt: skip
    body = {"amount": {"amount": amount, "currency": currency}, **extra}
    return _ok(person.post(f"/transactions/{txn}/refunds", body), status)


# --- Refunds


def test_partial_and_full_refunds_reduce_spending_never_income(people: list[Person]) -> None:
    alice, bob, _, _ = people
    bank = _setup(alice)
    groceries = _spend(alice, bank, "150.00")
    assert _balance(alice, bank) == "850.00"
    first = _refund(alice, groceries["id"], "50.00", description="Spoiled milk")
    assert first["refunds"]["refunded"]["amount"] == "50.00"
    assert first["refunds"]["refundable"]["amount"] == "100.00"
    refund_row = first["refunds"]["items"][0]
    assert (refund_row["type"], refund_row["refunds_transaction_id"]) == ("refund", groceries["id"])
    assert refund_row["category"]["system_key"] == "groceries"  # the purchase's category
    assert _balance(alice, bank) == "900.00"
    home = _summary(alice)
    assert (home["expenses"]["amount"], home["income"]["amount"]) == ("100.00", "0.00")
    b = _report(alice)["balances"]
    assert (b["refunds"]["amount"], b["income"]["amount"], b["reconciles"]) == (
        "50.00", "0.00", True,
    )  # fmt: skip
    # More than what's left is refused, with the amount that's left.
    over = alice.post(f"/transactions/{groceries['id']}/refunds",
                      {"amount": {"amount": "100.01", "currency": "EUR"}})  # fmt: skip
    assert (over.status_code, over.json()["code"]) == (409, "refund_exceeds_original")
    assert over.json()["params"]["refundable"] == "100.00"
    second = _refund(alice, groceries["id"], "100.00")
    assert second["refunds"]["refundable"]["amount"] == "0.00"
    assert len(second["refunds"]["items"]) == 2
    # The purchase stays as it was and can't be voided or re-priced under its refunds.
    detail = _ok(alice.get(f"/transactions/{groceries['id']}"))
    assert (detail["status"], detail["amount"]["amount"]) == ("posted", "150.00")
    void = alice.c.post(
        f"{API}/transactions/{groceries['id']}/void",
        headers={**alice.h(False), "If-Match": f'"{detail["version"]}"'},
    )
    assert (void.status_code, void.json()["code"]) == (409, "has_refunds")
    edit = alice.c.patch(
        f"{API}/transactions/{groceries['id']}",
        json={"amount": {"amount": "10.00", "currency": "EUR"}},
        headers={**alice.h(False), "If-Match": f'"{detail["version"]}"'},
    )
    assert (edit.status_code, edit.json()["code"]) == (409, "has_refunds")
    # Voiding a refund makes that amount refundable again.
    last = second["refunds"]["items"][1]
    _ok(alice.c.post(f"{API}/transactions/{last['id']}/void",
                     headers={**alice.h(False), "If-Match": f'"{last["version"]}"'}))  # fmt: skip
    again = _ok(alice.get(f"/transactions/{groceries['id']}"))
    assert again["refunds"]["refundable"]["amount"] == "100.00"
    assert [i["status"] for i in again["refunds"]["items"]] == ["posted", "reversed"]
    assert _summary(alice)["expenses"]["amount"] == "100.00"
    # Listed with its own type; someone else can't refund it.
    assert len(_ok(alice.get("/transactions", type="refund"))["items"]) == 1  # live only
    assert len(_ok(alice.get("/transactions", type="refund", include_voided=True))["items"]) == 2
    assert (
        bob.post(
            f"/transactions/{groceries['id']}/refunds",
            {"amount": {"amount": "1.00", "currency": "EUR"}},
        ).status_code
        == 404
    )


def test_a_refund_counts_in_its_own_month_and_respects_dates(people: list[Person]) -> None:
    alice, _, _, _ = people
    bank = _setup(alice)
    old = _spend(alice, bank, "150.00", when=LAST_MONTH_DAY)
    early = alice.post(f"/transactions/{old['id']}/refunds", {
        "amount": {"amount": "10.00", "currency": "EUR"},
        "effective_date": (LAST_MONTH_DAY - timedelta(days=1)).isoformat(),
    })  # fmt: skip
    assert (early.status_code, early.json()["code"]) == (422, "refund_before_purchase")
    _refund(alice, old["id"], "50.00")
    last = _ok(alice.get("/reports", **{"from": LAST_MONTH_DAY.replace(day=1).isoformat(),
                                        "to": LAST_MONTH_DAY.isoformat()}))  # fmt: skip
    assert last["by_currency"][0]["totals"]["expenses"]["amount"] == "150.00"  # history kept
    this = _report(alice)
    assert this["totals"]["expenses"]["amount"] == "-50.00"  # shown as such (§4.4)
    # A budget for groceries this month sees the refund too.
    body = {"category_id": _category_id(alice, "groceries"),
            "limit": {"amount": "100.00", "currency": "EUR"},
            "from_month": MONTH.strftime("%Y-%m")}  # fmt: skip
    _ok(alice.c.put(f"{API}/budgets", json=body, headers=alice.h(False)))
    budget = _ok(alice.get("/budgets", month=MONTH.strftime("%Y-%m")))["budgets"][0]
    assert budget["spent"]["amount"] == "-50.00"


def test_foreign_currency_refunds_convert_explicitly(people: list[Person]) -> None:
    alice, _, _, _ = people
    _ok(alice.post("/setup", {"currency": "TRY"}, key=False))
    lira = _ok(alice.post("/accounts", {"name": "Lira", "type": "bank", "currency": "TRY",
                                         "opening_balance": {"amount": "5000.00",
                                                             "currency": "TRY"}}), 201)  # fmt: skip
    euro = _ok(alice.post("/accounts", {"name": "Euro", "type": "bank", "currency": "EUR"}), 201)
    shoes = _ok(alice.post("/transactions", {
        "type": "expense", "account_id": lira["id"],
        "amount": {"amount": "10.00", "currency": "EUR"},
        "category_id": _category_id(alice, "shopping"),
        "conversion": {"method": "manual_rate", "rate": "35"},
    }), 201)  # fmt: skip
    assert shoes["amount"]["amount"] == "350.00"
    # Entered in euros at a better rate, it would exceed what was paid: refused.
    too_much = alice.post(f"/transactions/{shoes['id']}/refunds", {
        "amount": {"amount": "10.00", "currency": "EUR"},
        "conversion": {"method": "manual_rate", "rate": "36"},
    })  # fmt: skip
    assert too_much.json()["code"] == "refund_exceeds_original"
    half = _refund(
        alice,
        shoes["id"],
        "5.00",
        "EUR",
        conversion={"method": "actual_amount", "account_amount": "175.00"},
    )
    item = half["refunds"]["items"][0]
    assert (item["amount"]["amount"], item["amount"]["currency"]) == ("175.00", "TRY")
    assert item["original_amount"] == {"amount": "5.00", "currency": "EUR"}
    # A conversion is needed when the amount isn't in the account's currency.
    missing = alice.post(f"/transactions/{shoes['id']}/refunds",
                         {"amount": {"amount": "1.00", "currency": "EUR"}})  # fmt: skip
    assert missing.json()["code"] == "conversion_required"
    # The refund goes to an account in the purchase's currency.
    elsewhere = alice.post(f"/transactions/{shoes['id']}/refunds",
                           {"amount": {"amount": "1.00", "currency": "EUR"},
                            "account_id": euro["id"]})  # fmt: skip
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (422, "refund_currency_mismatch")


def test_concurrent_refunds_never_exceed_the_purchase(people: list[Person]) -> None:
    alice, _, _, _ = people
    bank = _setup(alice)
    txn = _spend(alice, bank, "150.00")
    results: list[int] = []
    barrier = threading.Barrier(4)

    def go() -> None:
        barrier.wait()
        response = alice.post(f"/transactions/{txn['id']}/refunds",
                              {"amount": {"amount": "60.00", "currency": "EUR"}})  # fmt: skip
        results.append(response.status_code)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 201, 409, 409]
    assert _balance(alice, bank) == "970.00"  # 1000 - 150 + 60 + 60
    # The same key replays the same refund.
    key = str(uuid.uuid4())
    other = _spend(alice, bank, "20.00")
    headers = {**alice.h(False), "Idempotency-Key": key}
    body = {"amount": {"amount": "5.00", "currency": "EUR"}}
    one = alice.c.post(f"{API}/transactions/{other['id']}/refunds", json=body, headers=headers)
    two = alice.c.post(f"{API}/transactions/{other['id']}/refunds", json=body, headers=headers)
    assert one.status_code == two.status_code == 201
    assert two.headers.get("Idempotent-Replayed") == "true"
    assert _ok(alice.get(f"/transactions/{other['id']}"))["refunds"]["refunded"]["amount"] == "5.00"


# --- Quick templates


def test_templates_are_shortcuts_that_never_post(people: list[Person]) -> None:
    alice, bob, _, _ = people
    bank = _setup(alice)
    before = _ok(alice.get("/transactions"))["items"]
    coffee = _ok(alice.post("/templates", {
        "name": "Coffee", "kind": "expense", "amount": {"amount": "3.50", "currency": "EUR"},
        "account_id": bank["id"], "category_id": _category_id(alice, "food_and_drinks"),
        "is_favourite": True,
    }), 201)  # fmt: skip
    bread = _ok(alice.post("/templates", {
        "name": "Bread", "kind": "expense", "currency": "EUR",
        "category_id": _category_id(alice, "groceries"),
    }), 201)  # fmt: skip
    assert bread["amount"] is None and bread["account_id"] is None  # asked each time
    salary = _spend(alice, bank, "12.00", category="groceries", description="Market")
    saved = _ok(alice.post("/templates", {"from_transaction_id": salary["id"]}), 201)
    assert (saved["name"], saved["amount"]["amount"]) == ("Market", "12.00")
    assert _ok(alice.get("/transactions"))["items"] != before  # only the expense above
    assert len(_ok(alice.get("/transactions"))["items"]) == len(before) + 1
    names = [t["name"] for t in _ok(alice.get("/templates"))]
    assert names[0] == "Coffee"  # favourites first
    # Edit with If-Match; archive; delete.
    edited = _ok(
        alice.c.patch(
            f"{API}/templates/{bread['id']}",
            json={"amount": {"amount": "2.20", "currency": "EUR"}, "is_favourite": True},
            headers={**alice.h(False), "If-Match": f'"{bread["version"]}"'},
        )
    )
    assert edited["amount"]["amount"] == "2.20"
    stale = alice.c.patch(
        f"{API}/templates/{bread['id']}",
        json={"name": "x"},
        headers={**alice.h(False), "If-Match": f'"{bread["version"]}"'},
    )
    assert stale.status_code == 412
    archived = _ok(
        alice.c.patch(
            f"{API}/templates/{saved['id']}",
            json={"status": "archived"},
            headers={**alice.h(False), "If-Match": f'"{saved["version"]}"'},
        )
    )
    assert archived["status"] == "archived"
    assert saved["id"] not in [t["id"] for t in _ok(alice.get("/templates"))]
    used = _ok(alice.post(f"/templates/{coffee['id']}/used", key=False))
    assert used["use_count"] == 1
    assert len(_ok(alice.get("/transactions"))["items"]) == len(before) + 1  # still no posting
    assert (
        alice.c.delete(f"{API}/templates/{coffee['id']}", headers=alice.h(False)).status_code == 204
    )
    # Wrong kind, someone else's template.
    wrong = alice.post("/templates", {"name": "Pay", "kind": "income", "currency": "EUR",
                                      "category_id": _category_id(alice, "groceries")})  # fmt: skip
    assert wrong.json()["code"] == "category_kind_mismatch"
    assert bob.post(f"/templates/{bread['id']}/used", key=False).status_code == 404


# --- Savings goals


def test_goal_progress_is_tracked_never_money(
    people: list[Person], factory: "sessionmaker[Session]"
) -> None:
    alice, bob, _, _ = people
    bank = _setup(alice)
    goal = _ok(
        alice.post(
            "/goals",
            {
                "name": "New laptop",
                "target": {"amount": "1500.00", "currency": "EUR"},
                "target_date": "2027-06-30",
                "starting": "100.00",
            },
        ),
        201,
    )
    assert (goal["progress"]["amount"], goal["percent"]) == ("100.00", "6.7")
    goal = _ok(alice.post(f"/goals/{goal['id']}/add", {"amount": "400.00", "note": "Bonus"}))
    assert (goal["progress"]["amount"], goal["remaining"]["amount"], goal["percent"]) == (
        "500.00", "1000.00", "33.3",
    )  # fmt: skip
    too_much = alice.post(f"/goals/{goal['id']}/reduce", {"amount": "600.00"})
    assert (too_much.status_code, too_much.json()["code"]) == (409, "goal_progress_negative")
    goal = _ok(alice.post(f"/goals/{goal['id']}/reduce", {"amount": "100.00"}))
    assert goal["progress"]["amount"] == "400.00"
    assert _balance(alice, bank) == "1000.00"  # no money moved
    paused = _ok(
        alice.c.patch(
            f"{API}/goals/{goal['id']}",
            json={"status": "paused"},
            headers={**alice.h(False), "If-Match": f'"{goal["version"]}"'},
        )
    )
    blocked = alice.post(f"/goals/{goal['id']}/add", {"amount": "1.00"})
    assert blocked.json()["code"] == "goal_not_active"
    target = _ok(
        alice.c.patch(
            f"{API}/goals/{goal['id']}",
            json={"target": "800.00", "status": "active"},
            headers={**alice.h(False), "If-Match": f'"{paused["version"]}"'},
        )
    )
    assert (target["percent"], target["reached"]) == ("50.0", False)
    kinds = [e["kind"] for e in target["history"]]
    assert kinds[:3] == ["status_changed", "target_changed", "status_changed"]
    assert kinds[-1] == "created" and kinds.count("added") == 2 and kinds.count("reduced") == 1
    # History is append-only in the database itself.
    with factory() as db, pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE savings_goal_events SET note = 'x'"))
    with factory() as db, pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM savings_goal_events"))
    archived = _ok(
        alice.c.patch(
            f"{API}/goals/{goal['id']}",
            json={"status": "archived"},
            headers={**alice.h(False), "If-Match": f'"{target["version"]}"'},
        )
    )
    assert archived["status"] == "archived"
    assert _ok(alice.get("/goals")) == []
    assert len(_ok(alice.get("/goals", include_archived=True))) == 1
    assert bob.get(f"/goals/{goal['id']}").status_code == 404


# --- Category groups


def test_group_totals_regroup_category_totals_without_double_counting(
    people: list[Person],
) -> None:
    alice, _, _, _ = people
    bank = _setup(alice)
    groups = {g["system_key"]: g for g in _ok(alice.get("/category-groups"))}
    assert set(groups) == {"essentials", "lifestyle", "other"}
    cats = {c["system_key"]: c for c in _ok(alice.get("/categories"))}
    assert cats["groceries"]["group_id"] == groups["essentials"]["id"]
    assert cats["salary"]["group_id"] is None  # income isn't grouped
    _spend(alice, bank, "30.00", "groceries")
    _spend(alice, bank, "70.00", "housing")
    _spend(alice, bank, "20.00", "food_and_drinks")

    def totals() -> dict[str | None, str]:
        rows = _ok(alice.get("/category-groups/totals", currency="EUR"))
        return {r["group_id"]: r["amount"]["amount"] for r in rows}

    assert totals() == {groups["essentials"]["id"]: "100.00", groups["lifestyle"]["id"]: "20.00"}
    # Moving a category moves its whole total, once.
    _ok(
        alice.c.put(
            f"{API}/categories/{cats['food_and_drinks']['id']}/group",
            json={"group_id": groups["essentials"]["id"]},
            headers=alice.h(False),
        )
    )
    assert totals() == {groups["essentials"]["id"]: "120.00"}
    assert _summary(alice)["expenses"]["amount"] == "120.00"  # the overall figure is unchanged
    # Archiving a group leaves its categories ungrouped; every amount stays.
    essentials = groups["essentials"]
    _ok(
        alice.c.patch(
            f"{API}/category-groups/{essentials['id']}",
            json={"status": "archived"},
            headers={**alice.h(False), "If-Match": f'"{essentials["version"]}"'},
        )
    )
    assert totals() == {None: "120.00"}
    # Custom groups: create, rename, unique names; income categories can't be grouped.
    trips = _ok(alice.post("/category-groups", {"name": "Trips"}, key=False), 201)
    dup = alice.post("/category-groups", {"name": "trips"}, key=False)
    assert (dup.status_code, dup.json()["code"]) == (409, "group_name_taken")
    renamed = _ok(
        alice.c.patch(
            f"{API}/category-groups/{trips['id']}",
            json={"name": "Travel"},
            headers={**alice.h(False), "If-Match": f'"{trips["version"]}"'},
        )
    )
    assert renamed["name"] == "Travel"
    income = alice.c.put(f"{API}/categories/{cats['salary']['id']}/group",
                         json={"group_id": trips["id"]}, headers=alice.h(False))  # fmt: skip
    assert income.status_code == 422


# --- Calendar


def test_calendar_days_follow_the_persons_time_zone(people: list[Person]) -> None:
    alice, _, _, _ = people
    # Kiritimati is UTC+14: "today" there is often tomorrow in UTC.
    _ok(alice.c.patch(f"{API}/auth/me", json={"time_zone": "Pacific/Kiritimati"},
                      headers=alice.h(False)))  # fmt: skip
    bank = _setup(alice)
    me = _ok(alice.get("/calendar"))
    local_today = me["today"]
    _spend(alice, bank, "12.00")  # no date: today in her zone
    salary = _category_id(alice, "salary")
    _ok(alice.post("/transactions", {"type": "income", "account_id": bank["id"],
                                     "amount": {"amount": "500.00", "currency": "EUR"},
                                     "category_id": salary}), 201)  # fmt: skip
    cal = _ok(alice.get("/calendar", month=local_today[:7]))
    day = next(d for d in cal["days"] if d["day"] == local_today)
    assert (day["expenses"]["amount"], day["income"]["amount"], day["transactions"]) == (
        "12.00", "500.00", 3,  # opening balance, expense, income
    )  # fmt: skip
    assert cal["currency"] == "EUR" and cal["currencies"] == ["EUR"]
    other = [d for d in cal["days"] if d["day"] != local_today]
    assert all(d["expenses"]["amount"] == "0.00" for d in other)
    # A recurring obligation appears as expected, never as paid.
    rule = _ok(alice.post("/recurring/rules", {
        "kind": "expense", "name": "Rent", "amount": {"amount": "700.00", "currency": "EUR"},
        "currency": "EUR", "account_id": bank["id"], "category_id": _category_id(alice, "housing"),
        "frequency": "monthly", "window_start_day": 28, "window_end_day": 28,
    }), 201)  # fmt: skip
    nxt = rule["next"]
    cal = _ok(alice.get("/calendar", month=nxt["window_start"][:7]))
    assert [o["rule_name"] for o in cal["occurrences"]] == ["Rent"]
    assert cal["occurrences"][0]["state"] in ("expected", "pending")


# --- Subscriptions (the recurring engine)


def test_subscriptions_reuse_recurring_and_never_mark_paid(people: list[Person]) -> None:
    alice, _, _, _ = people
    bank = _setup(alice)
    subs = _category_id(alice, "subscriptions")

    def rule(name: str, amount: str | None, frequency: str, **extra: Any) -> Any:
        body = {
            "kind": "expense", "name": name, "currency": "EUR", "account_id": bank["id"],
            "category_id": subs, "frequency": frequency, "is_subscription": True,
            **({"amount": {"amount": amount, "currency": "EUR"}} if amount else {}), **extra,
        }  # fmt: skip
        return alice.post("/recurring/rules", body)

    day = TODAY.day
    netflix = _ok(rule("Netflix", "10.00", "monthly", window_start_day=day), 201)
    _ok(rule("Cloud", "119.99", "yearly", window_start_day=day, month=TODAY.month), 201)
    _ok(rule("Gym", None, "monthly", window_start_day=day), 201)
    weekly = rule("Paper", "2.00", "weekly")
    assert (weekly.status_code, weekly.json()["code"]) == (422, "invalid_subscription")
    view = _ok(alice.get("/subscriptions"))
    assert {i["name"] for i in view["items"]} == {"Netflix", "Cloud", "Gym"}
    (cost,) = view["costs"]
    # 10 + 119.99/12 (10.00 rounded half up) per month; 120 + 119.99 per year.
    assert (cost["monthly"]["amount"], cost["annual"]["amount"]) == ("20.00", "239.99")
    assert (cost["variable"], cost["approximate"]) == (1, True)
    assert len(view["upcoming"]) >= 2
    # Nothing has been paid: only the opening balance exists.
    assert len(_ok(alice.get("/transactions"))["items"]) == 1
    # Pausing takes it out of the cost; confirming posts exactly one payment.
    _ok(
        alice.c.post(
            f"{API}/recurring/rules/{netflix['id']}/pause",
            headers={**alice.h(False), "If-Match": f'"{netflix["version"]}"'},
        )
    )
    cost = _ok(alice.get("/subscriptions"))["costs"][0]
    assert cost["monthly"]["amount"] == "10.00"
    cloud = next(o for o in view["upcoming"] if o["rule_name"] == "Cloud")
    _ok(alice.post(f"/recurring/occurrences/{cloud['id']}/confirm",
                   {"amount": {"amount": "119.99", "currency": "EUR"}}))  # fmt: skip
    assert _balance(alice, bank) == "880.01"


# --- Trip Mode: cross-currency settlements


def test_a_payment_in_another_currency_counts_in_the_trip_currency(
    people: list[Person],
) -> None:
    alice, bob, carol, _ = people
    trip = _trip(alice, "EUR", [bob, carol])
    _expense(alice, trip, alice, "90.00", [alice, bob, carol])
    # Both amounts stated: €30 settled with 1,689.30 TRY.
    paid = _ok(bob.post(f"/trips/{trip}/settlements", {
        "recipient_user_id": alice.id, "amount": {"amount": "1689.30", "currency": "TRY"},
        "base_amount": "30.00",
    }), 201)  # fmt: skip
    assert paid["amount"] == {"amount": "1689.30", "currency": "TRY"}
    assert paid["base_amount"] == {"amount": "30.00", "currency": "EUR"}
    assert paid["rate"]["is_manual"] is True
    assert _nets(alice, trip)["Bob"] == "-30.00"  # pending: nothing moves
    _act(alice, trip, paid["id"], "confirm")
    assert _nets(alice, trip) == {"Alice": "30.00", "Bob": "0.00", "Carol": "-30.00"}
    # A stored reference rate (fake provider): 1 TRY = 0.017759 EUR.
    preview = _ok(carol.get("/fx/convert", **{"from": "TRY", "to": "EUR", "amount": "1689.30"}))
    ref = _ok(carol.post(f"/trips/{trip}/settlements", {
        "recipient_user_id": alice.id, "amount": {"amount": "1689.30", "currency": "TRY"},
        "rate": {"rate_id": preview["rate"]["id"]},
    }), 201)  # fmt: skip
    assert ref["base_amount"]["amount"] == preview["converted"]["amount"] == "30.00"
    assert ref["rate"]["is_manual"] is False
    # No rate, or a rate on a trip-currency payment: refused, nothing invented.
    none = bob.post(
        f"/trips/{trip}/settlements",
        {"recipient_user_id": alice.id, "amount": {"amount": "10.00", "currency": "TRY"}},
    )
    assert (none.status_code, none.json()["code"]) == (422, "rate_required")
    needless = bob.post(
        f"/trips/{trip}/settlements",
        {
            "recipient_user_id": alice.id,
            "amount": {"amount": "10.00", "currency": "EUR"},
            "base_amount": "10.00",
        },
    )
    assert needless.json()["code"] == "conversion_not_needed"
    # The recipient confirms; the reversal undoes exactly the same values.
    _act(alice, trip, ref["id"], "confirm")
    reversal = _act(alice, trip, paid["id"], "reverse", status=201)
    assert (reversal["amount"], reversal["base_amount"]) == (paid["amount"], paid["base_amount"])
    _act(bob, trip, reversal["id"], "confirm")
    assert _nets(alice, trip)["Bob"] == "-30.00"
    # Recorded values never change, whatever rates do later.
    _ok(
        bob.post(
            f"/trips/{trip}/settlements",
            {
                "recipient_user_id": alice.id,
                "amount": {"amount": "10.00", "currency": "TRY"},
                "rate": {"manual": "0.5"},
            },
        ),
        201,
    )
    again = _ok(alice.get(f"/trips/{trip}/settlements/{paid['id']}"))
    assert again["base_amount"]["amount"] == "30.00" and again["amount"]["amount"] == "1689.30"
    # The report counts the trip-currency value and shows what was paid.
    report = _ok(alice.get(f"/trips/{trip}/reports/summary"))
    first = next(p for p in report["settlements_confirmed"] if p["id"] == ref["id"])
    assert (first["amount"]["amount"], first["paid"]["currency"]) == ("30.00", "TRY")


def test_the_recipient_records_a_converted_payment_in_their_own_currency(
    people: list[Person],
) -> None:
    alice, bob, _, _ = people
    _ok(alice.post("/setup", {"currency": "TRY"}, key=False))
    lira = _ok(alice.post("/accounts", {"name": "Lira", "type": "bank", "currency": "TRY"}), 201)
    trip = _trip(alice, "EUR", [bob])
    _expense(alice, trip, alice, "60.00", [alice, bob])
    paid = _ok(
        bob.post(
            f"/trips/{trip}/settlements",
            {
                "recipient_user_id": alice.id,
                "amount": {"amount": "1689.30", "currency": "TRY"},
                "base_amount": "30.00",
            },
        ),
        201,
    )
    _act(alice, trip, paid["id"], "confirm")
    link = _link(alice, trip, lira, "1689.30", settlement_id=paid["id"])
    assert link["linked_base_amount"] == {"amount": "-30.00", "currency": "EUR"}
    assert _balance(alice, lira) == "1689.30"
    assert _ok(alice.get(f"/me/trips/{trip}/position"))["unrecorded"]["amount"] == "60.00"


# --- Trip Mode: one-step personal linking


def test_the_payer_records_an_expense_and_their_cash_in_one_step(
    people: list[Person], factory: "sessionmaker[Session]"
) -> None:
    alice, bob, _, _ = people
    bank = _setup(alice)
    b_bank = _setup(bob)
    trip = _trip(alice, "EUR", [bob])

    def expense_body(payer: Person, amount: str = "40.00") -> dict[str, Any]:
        return {
            "payer_user_id": payer.id, "description": "Taxi", "category_key": "transportation",
            "expense_date": TODAY.isoformat(), "amount": {"amount": amount, "currency": "EUR"},
            "split": {"method": "equal", "shares": [{"user_id": alice.id}, {"user_id": bob.id}]},
        }  # fmt: skip

    done = _ok(
        alice.post(
            "/links/expense",
            {
                "trip_id": trip,
                "expense": expense_body(alice),
                "link": {"account_id": bank["id"], "amount": "40.00"},
            },
        ),
        201,
    )
    assert done["expense"]["status"] == "approved" and done["link"]["status"] == "active"
    assert _balance(alice, bank) == "960.00"
    # Nobody can choose an account for someone else's payment.
    for_alice = bob.post(
        "/links/expense",
        {
            "trip_id": trip,
            "expense": expense_body(alice),
            "link": {"account_id": b_bank["id"], "amount": "40.00"},
        },
    )
    assert (for_alice.status_code, for_alice.json()["code"]) == (403, "not_payer")
    # An invalid account undoes the whole request: no expense is left behind.
    with factory() as db:
        before: int = db.execute(text("SELECT count(*) FROM trip_expenses")).scalar_one()
    broken = alice.post(
        "/links/expense",
        {
            "trip_id": trip,
            "expense": expense_body(alice, "15.00"),
            "link": {"account_id": b_bank["id"], "amount": "15.00"},
        },
    )
    assert broken.status_code == 404
    with factory() as db:
        assert db.execute(text("SELECT count(*) FROM trip_expenses")).scalar_one() == before
    # Bob records one Alice paid: it waits for her; she approves and records it at once.
    proposed = _expense(bob, trip, alice, "30.00", [alice, bob], description="Museum")
    assert proposed["status"] == "pending_payer"
    version = proposed["pending_version"]["id"]
    not_mine = bob.post("/links/approve", {"trip_id": trip, "version_id": version,
                                           "link": {"account_id": b_bank["id"],
                                                    "amount": "30.00"}})  # fmt: skip
    assert not_mine.status_code == 403
    assert _balance(alice, bank) == "960.00"  # untouched until she approves
    approved = _ok(alice.post("/links/approve", {"trip_id": trip, "version_id": version,
                                                 "link": {"account_id": bank["id"],
                                                          "amount": "30.00"}}))  # fmt: skip
    assert approved["expense"]["status"] == "approved"
    assert _balance(alice, bank) == "930.00"
    # Linked once only.
    again = alice.post(
        "/links/approve",
        {
            "trip_id": trip,
            "version_id": version,
            "link": {"account_id": bank["id"], "amount": "30.00"},
        },
    )
    assert again.status_code in (409,)
    # Bob's view of the trip shows nothing of Alice's account.
    for path in ("/expenses", "/dashboard", "/activity", "/reports/summary"):
        body = bob.get(f"/trips/{trip}{path}").text
        assert bank["id"] not in body and "960.00" not in body and "930.00" not in body
