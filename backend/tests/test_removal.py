"""Deleting, archiving and restoring across FinPilot: permanent deletion only when it's financially
safe, a clear reason when it isn't, and history always kept. Also Home's saved layout and the Total
Balance (accounts only, never liabilities)."""

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.account import HomeLayout, upgrade_layout
from app.db.models import User
from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_installments import _plan
from tests.test_links import _balance, _category_id, _setup
from tests.test_trips import API, Person, _ok, people  # noqa: F401 - fixtures


def _delete(person: Person, path: str, version: int | None = None) -> Any:
    headers = person.h(False)
    if version is not None:
        headers["If-Match"] = f'"{version}"'
    return person.c.delete(f"{API}{path}", headers=headers)


def _account(person: Person, name: str, currency: str = "EUR", opening: str | None = None) -> Any:
    body: dict[str, Any] = {"name": name, "type": "wallet", "currency": currency}
    if opening:
        body["opening_balance"] = {"amount": opening, "currency": currency}
    return _ok(person.post("/accounts", body), 201)


def _rule(person: Person, account: dict[str, Any], **over: Any) -> Any:
    body: dict[str, Any] = {
        "kind": "expense", "name": "Gym", "amount": {"amount": "30.00", "currency": "EUR"},
        "currency": "EUR", "account_id": account["id"],
        "category_id": _category_id(person, "health"), "frequency": "monthly",
        "window_start_day": 1, "window_end_day": 31,
        "start_date": TODAY.replace(day=1).isoformat(), **over,
    }  # fmt: skip
    return _ok(person.post("/recurring/rules", body), 201)


# --- Accounts


def test_an_unused_account_is_deleted_and_a_used_one_is_only_archived(
    people: list[Person],
) -> None:
    alice, bob, *_ = people
    bank = _setup(alice)
    spare = _account(alice, "Spare")
    # Someone else can't delete it.
    _setup(bob)
    assert _delete(bob, f"/accounts/{spare['id']}").status_code == 404
    assert _delete(alice, f"/accounts/{spare['id']}").status_code == 204
    assert alice.get(f"/accounts/{spare['id']}").status_code == 404
    # An account with entries can only be archived, and it says so.
    refused = _delete(alice, f"/accounts/{bank['id']}")
    assert (refused.status_code, refused.json()["code"]) == (409, "account_has_entries")
    assert _balance(alice, bank) == "1000.00"


def test_an_account_a_template_or_recurring_item_uses_says_so(people: list[Person]) -> None:
    alice, *_ = people
    _setup(alice)
    wallet = _account(alice, "Wallet")
    food = _category_id(alice, "food_and_drinks")
    coffee = {"name": "Coffee", "kind": "expense", "currency": "EUR",
              "account_id": wallet["id"], "category_id": food}  # fmt: skip
    _ok(alice.post("/templates", coffee), 201)
    refused = _delete(alice, f"/accounts/{wallet['id']}")
    assert (refused.status_code, refused.json()["code"]) == (409, "account_used_by_template")
    other = _account(alice, "Other")
    _rule(alice, other)
    refused = _delete(alice, f"/accounts/{other['id']}")
    assert (refused.status_code, refused.json()["code"]) == (409, "account_used_by_recurring")


def test_deleting_the_default_account_clears_the_preference(people: list[Person]) -> None:
    alice, *_ = people
    _setup(alice)
    spare = _account(alice, "Spare")
    alice.c.patch(f"{API}/auth/me", json={"default_account_id": spare["id"]},
                  headers=alice.h(False))  # fmt: skip
    assert _delete(alice, f"/accounts/{spare['id']}").status_code == 204


# --- Categories


def test_only_an_unused_custom_category_can_be_deleted(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice)
    new = _ok(alice.post("/categories", {"kind": "expense", "name": "Pets", "icon": "dog",
                                         "color": "amber"}), 201)  # fmt: skip
    assert _delete(alice, f"/categories/{new['id']}").status_code == 204
    default = _delete(alice, f"/categories/{_category_id(alice, 'groceries')}")
    assert (default.status_code, default.json()["code"]) == (409, "category_is_default")
    used = _ok(alice.post("/categories", {"kind": "expense", "name": "Hobbies", "icon": "music",
                                          "color": "violet"}), 201)  # fmt: skip
    txn = _ok(alice.post("/transactions", {
        "type": "expense", "account_id": bank["id"],
        "amount": {"amount": "5.00", "currency": "EUR"}, "category_id": used["id"],
    }), 201)  # fmt: skip
    # Even voided, the entry keeps pointing at it: archive instead.
    _ok(alice.c.post(f"{API}/transactions/{txn['id']}/void",
                     headers={**alice.h(False), "If-Match": f'"{txn["version"]}"'}))  # fmt: skip
    refused = _delete(alice, f"/categories/{used['id']}")
    assert (refused.status_code, refused.json()["code"]) == (409, "category_has_entries")
    budgeted = _ok(alice.post("/categories", {"kind": "expense", "name": "Books", "icon": "book",
                                              "color": "teal"}), 201)  # fmt: skip
    _ok(alice.c.put(f"{API}/budgets", json={
        "category_id": budgeted["id"], "limit": {"amount": "20.00", "currency": "EUR"},
        "from_month": TODAY.strftime("%Y-%m"),
    }, headers=alice.h(False)))  # fmt: skip
    refused = _delete(alice, f"/categories/{budgeted['id']}")
    assert refused.json()["code"] == "category_used_by_budget"


# --- Recurring items and subscriptions


def test_a_recurring_item_without_payments_is_deleted_one_with_payments_is_ended(
    people: list[Person],
) -> None:
    alice, bob, *_ = people
    bank = _setup(alice)
    _setup(bob)
    mistake = _rule(alice, bank, name="Typo", is_subscription=True)
    assert _delete(bob, f"/recurring/rules/{mistake['id']}", mistake["version"]).status_code == 404
    stale = _delete(alice, f"/recurring/rules/{mistake['id']}", mistake["version"] + 5)
    assert stale.status_code == 412
    assert (
        _delete(alice, f"/recurring/rules/{mistake['id']}", mistake["version"]).status_code == 204
    )
    assert all(o["rule_id"] != mistake["id"] for o in _ok(alice.get("/recurring/occurrences")))

    paid = _rule(alice, bank, name="Gym")
    occurrence = next(o for o in _ok(alice.get("/recurring/occurrences"))
                      if o["rule_id"] == paid["id"])  # fmt: skip
    _ok(alice.post(f"/recurring/occurrences/{occurrence['id']}/confirm",
                   {"amount": {"amount": "30.00", "currency": "EUR"}}))  # fmt: skip
    fresh = next(r for r in _ok(alice.get("/recurring/rules")) if r["id"] == paid["id"])
    refused = _delete(alice, f"/recurring/rules/{paid['id']}", fresh["version"])
    assert (refused.status_code, refused.json()["code"]) == (409, "rule_has_payments")
    # The payment is still there.
    assert _balance(alice, bank) == "970.00"


# --- Templates


def test_templates_are_deleted_and_only_by_their_owner(people: list[Person]) -> None:
    alice, bob, *_ = people
    _setup(alice)
    _setup(bob)
    lunch = {"name": "Lunch", "kind": "expense", "currency": "EUR",
             "category_id": _category_id(alice, "food_and_drinks")}  # fmt: skip
    template = _ok(alice.post("/templates", lunch), 201)
    assert _delete(bob, f"/templates/{template['id']}").status_code == 404
    assert _delete(alice, f"/templates/{template['id']}").status_code == 204
    assert _delete(alice, f"/templates/{template['id']}").status_code == 404


# --- Home layout


def test_home_layout_is_saved_per_person_and_can_be_reset(people: list[Person]) -> None:
    alice, bob, *_ = people
    assert _ok(alice.get("/auth/me/home-layout"))["layout"] is None
    layout = {
        "rows": [["summary"], ["recent", "categories", "upcoming"]],
        "hidden": ["links", "goals"],
    }
    saved = _ok(alice.c.put(f"{API}/auth/me/home-layout", json=layout, headers=alice.h(False)))
    assert saved["layout"] == layout
    assert _ok(alice.get("/auth/me/home-layout"))["layout"] == layout
    assert _ok(bob.get("/auth/me/home-layout"))["layout"] is None  # per person
    for bad, code in (
        ({"rows": [["summary"], ["nope"]]}, "unknown_widget"),
        ({"rows": [["summary", "recent"], ["recent"]]}, "duplicate_widget"),
        ({"rows": [["recent"]], "hidden": ["recent"]}, "duplicate_widget"),
    ):
        refused = alice.c.put(f"{API}/auth/me/home-layout", json=bad, headers=alice.h(False))
        assert (refused.status_code, refused.json()["code"]) == (422, code)
    reset = _ok(alice.c.put(f"{API}/auth/me/home-layout", headers=alice.h(False)))
    assert reset["layout"] is None


# --- Total Balance (Home)


def test_total_balance_is_accounts_only_never_debts(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice, opening="20000.00")
    _account(alice, "Lira", "TRY", "5631.00")
    _plan(alice, eur, item_price="10000.00", down_payment="1000.00")  # a 9000 liability
    body = _ok(alice.get("/balances/total", target="EUR"))
    assert set(body) == {"target", "accounts", "balances", "missing_rates", "is_stale", "rates"}
    assert {m["currency"]: m["amount"] for m in body["balances"]["by_currency"]} == {
        "EUR": "19000.00", "TRY": "5631.00",
    }  # fmt: skip
    assert body["balances"]["combined"]["amount"] == "19100.00"  # never minus the 9000 debt


def test_total_balance_shows_negative_and_large_balances_and_missing_rates(
    people: list[Person],
) -> None:
    alice, *_ = people
    _setup(alice, opening="999999999999.99")
    over = _account(alice, "Overdrawn")
    _ok(alice.post(f"/accounts/{over['id']}/adjustments", {
        "target_balance": {"amount": "-250.50", "currency": "EUR"},
        "expected_balance": {"amount": "0.00", "currency": "EUR"},
    }), 201)  # fmt: skip
    body = _ok(alice.get("/balances/total", target="EUR"))
    assert body["balances"]["combined"]["amount"] == "999999999749.49"
    _account(alice, "Yen", "JPY", "1000")
    missing = _ok(alice.get("/balances/total", target="EUR"))
    assert missing["missing_rates"] == ["JPY"] and missing["balances"]["combined"] is None
    assert {m["currency"] for m in missing["balances"]["by_currency"]} == {"EUR", "JPY"}


def test_a_layout_saved_before_the_redesign_is_read_as_rows() -> None:
    old = {
        "columns": [
            ["templates", "quick_add", "recent"],
            ["upcoming", "total_balance", "plans", "month", "budgets"],
        ],
        "hidden": ["links"],
    }
    upgraded = upgrade_layout(old)
    assert upgraded == {
        "rows": [
            ["summary"],
            ["templates", "upcoming"],
            ["recent"],
            ["goals"],
            ["categories"],
            ["budgets"],
        ],
        "hidden": ["links"],
    }
    assert HomeLayout.model_validate(upgraded).hidden == ["links"]
    assert upgrade_layout(upgraded) == upgraded


def test_a_hidden_total_balance_stays_hidden_after_the_upgrade() -> None:
    old = {"columns": [["quick_add", "recent"], ["month"]], "hidden": ["total_balance"]}
    assert upgrade_layout(old) == {"rows": [["recent", "categories"]], "hidden": ["summary"]}


def test_a_stored_two_column_layout_comes_back_through_the_api(
    people: list[Person], factory: sessionmaker[Session]
) -> None:
    alice, *_ = people
    me = _ok(alice.get("/auth/me"))["id"]
    old = {"columns": [["quick_add", "recent"], ["month"]], "hidden": ["links"]}
    with factory() as db:
        db.execute(update(User).where(User.id == uuid.UUID(me)).values(home_layout=old))
        db.commit()
    answer = _ok(alice.get("/auth/me/home-layout"))
    assert answer["layout"] == {"rows": [["recent", "categories"]], "hidden": ["links"]}
    # Saving writes the new shape.
    saved = _ok(
        alice.c.put(f"{API}/auth/me/home-layout", json=answer["layout"], headers=alice.h(False))
    )
    assert saved["layout"] == answer["layout"]
    with factory() as db:
        stored = db.scalar(select(User.home_layout).where(User.id == uuid.UUID(me)))
    assert stored == answer["layout"]
