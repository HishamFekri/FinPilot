"""Installment plans (FINANCIAL_RULES.md §4.13): the purchase counts once as spending, principal
repayments never do, interest and fees are their own expenses, the schedule never posts, and which
installments are paid comes only from confirmed payments.
"""

from datetime import timedelta
from typing import Any

import pytest

from tests.test_fx import TODAY, providers  # noqa: F401 - the fixture
from tests.test_links import _balance, _category_id, _setup, _summary
from tests.test_trips import API, Person, _ok, people  # noqa: F401 - fixtures

SOON = TODAY + timedelta(days=10)


def _plan(person: Person, account: dict[str, Any] | None, status: int = 201, **body: Any) -> Any:
    base: dict[str, Any] = {
        "origin": "purchase",
        "name": "Car",
        "lender": "City Bank",
        "currency": "EUR",
        "installment_count": 8,
        "first_due_date": SOON.isoformat(),
        "item_price": "10000.00",
        "down_payment": "2000.00",
        "category_id": _category_id(person, "transportation"),
        **({"account_id": account["id"]} if account else {}),
    }
    base.update(body)
    return _ok(person.post("/installments", {k: v for k, v in base.items() if v is not None}),
               status)  # fmt: skip


def _pay(person: Person, plan: dict[str, Any], account: dict[str, Any], status: int = 201,
         **body: Any) -> Any:  # fmt: skip
    return _ok(person.post(f"/installments/{plan['id']}/payments",
                           {"account_id": account["id"], **body}), status)  # fmt: skip


def _states(plan: dict[str, Any]) -> list[str]:
    return [i["state"] for i in plan["items"]]


def _change(person: Person, plan: dict[str, Any], action: str, body: Any = None) -> Any:
    return person.c.post(
        f"{API}/installments/{plan['id']}/{action}",
        json=body,
        headers={**person.h(False), "If-Match": f'"{plan["version"]}"'},
    )


def test_a_purchase_counts_its_full_price_once_and_the_account_pays_only_the_deposit(
    people: list[Person],
) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    plan = _plan(alice, bank)
    assert (plan["origin"], plan["principal"]["amount"], plan["remaining"]["amount"]) == (
        "purchase", "8000.00", "8000.00",
    )  # fmt: skip
    assert (plan["paid_count"], plan["remaining_count"], plan["installment_count"]) == (0, 8, 8)
    assert [i["principal"]["amount"] for i in plan["items"]] == ["1000.00"] * 8
    assert plan["items"][0]["due_date"] == SOON.isoformat()
    assert set(_states(plan)) == {"upcoming"}
    # The account lost only the down payment; spending shows the full price, in this month.
    assert _balance(alice, bank) == "18000.00"
    month = _summary(alice)
    assert (month["expenses"]["amount"], month["income"]["amount"]) == ("10000.00", "0.00")
    # The schedule posts nothing: nothing else changed.
    assert len(_ok(alice.get("/transactions"))["items"]) == 3  # opening, purchase, debt start


def test_principal_is_never_spending_and_interest_is_its_own_expense(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    plan = _plan(alice, bank, interest_per_installment="40.00", fee_per_installment="10.00")
    assert plan["interest_mode"] == "fixed"
    assert plan["next_due"]["total"]["amount"] == "1050.00"
    paid = _pay(alice, plan, bank, principal="1000.00", interest="40.00", fee="10.00")
    assert _states(paid)[:2] == ["paid", "upcoming"]
    assert (paid["paid_count"], paid["remaining"]["amount"]) == (1, "7000.00")
    assert (paid["interest_paid"]["amount"], paid["fees_paid"]["amount"]) == ("40.00", "10.00")
    assert _balance(alice, bank) == "16950.00"
    # Only interest and the fee are new spending, in "Interest & fees".
    assert _summary(alice)["expenses"]["amount"] == "10050.00"
    interest = _ok(alice.get("/transactions", category_id=_category_id(alice, "interest_fees")))
    assert sorted(t["amount"]["amount"] for t in interest["items"]) == ["10.00", "40.00"]
    payment = paid["payments"][0]
    assert (payment["principal"]["amount"], payment["voided"]) == ("1000.00", False)


def test_partial_early_and_extra_payments_cover_the_oldest_installments_first(
    people: list[Person],
) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    plan = _plan(alice, bank)
    plan = _pay(alice, plan, bank, principal="400.00")
    assert _states(plan)[:2] == ["partial", "upcoming"]
    assert plan["items"][0]["covered"]["amount"] == "400.00"
    assert plan["paid_count"] == 0
    # Early and extra: 2600 more covers the rest of #1, #2, #3 and part of #4.
    plan = _pay(alice, plan, bank, principal="2600.00")
    assert _states(plan)[:4] == ["paid", "paid", "paid", "upcoming"]
    assert plan["paid_count"] == 3 and plan["remaining_count"] == 5
    assert plan["remaining"]["amount"] == "5000.00"
    # Paid ahead of the schedule (nothing was due yet).
    assert plan["ahead"]["amount"] == "3000.00"
    # Never more than remains.
    over = alice.post(f"/installments/{plan['id']}/payments",
                      {"account_id": bank["id"], "principal": "5000.01"})  # fmt: skip
    assert (over.status_code, over.json()["code"]) == (422, "exceeds_remaining")
    done = _pay(alice, plan, bank, principal="5000.00")
    assert done["settled"] and set(_states(done)) == {"paid"}
    assert done["remaining_count"] == 0


def test_missed_installments_are_overdue_until_paid(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    plan = _plan(alice, bank, first_due_date=(TODAY - timedelta(days=40)).isoformat())
    assert _states(plan)[:2] == ["overdue", "overdue"] or _states(plan)[0] == "overdue"
    overdue = plan["overdue_count"]
    assert overdue >= 1
    assert plan["behind"]["amount"] == f"{overdue * 1000}.00"
    paid = _pay(alice, plan, bank, principal=f"{overdue * 1000}.00")
    assert paid["overdue_count"] == 0 and paid["behind"]["amount"] == "0.00"


def test_voiding_a_payment_reopens_its_installment(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    plan = _pay(alice, _plan(alice, bank), bank, principal="1000.00")
    tx = plan["payments"][0]["principal_transaction_id"]
    detail = _ok(alice.get(f"/transactions/{tx}"))
    _ok(alice.c.post(f"{API}/transactions/{tx}/void",
                     headers={**alice.h(False), "If-Match": f'"{detail["version"]}"'}))  # fmt: skip
    again = _ok(alice.get(f"/installments/{plan['id']}"))
    assert _states(again)[0] == "upcoming"
    assert (again["remaining"]["amount"], again["payments"][0]["voided"]) == ("8000.00", True)
    assert _balance(alice, bank) == "18000.00"


def test_a_plan_recorded_later_isnt_spending_and_counts_installments_already_paid(
    people: list[Person],
) -> None:
    alice, *_ = people
    bank = _setup(alice)
    plan = _plan(alice, None, origin="existing", item_price=None, down_payment=None,
                 category_id=None, principal="3000.00", installment_count=8,
                 paid_before_count=5)  # fmt: skip
    assert plan["origin"] == "existing"
    assert (plan["paid_count"], plan["remaining_count"]) == (5, 3)
    assert [i["sequence"] for i in plan["items"]] == [6, 7, 8]
    assert _balance(alice, bank) == "1000.00"
    assert _summary(alice).get("expenses", {}).get("amount", "0.00") == "0.00"


def test_a_cash_loan_is_never_income(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice)
    plan = _plan(alice, bank, origin="loan", item_price=None, down_payment=None, category_id=None,
                 principal="1200.00", installment_count=12)  # fmt: skip
    assert plan["origin"] == "loan"
    assert _balance(alice, bank) == "2200.00"
    month = _summary(alice)
    assert month.get("income", {}).get("amount", "0.00") == "0.00"


def test_rescheduling_supersedes_the_unpaid_installments_and_keeps_them(
    people: list[Person],
) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    plan = _pay(alice, _plan(alice, bank), bank, principal="1500.00")
    later = (SOON + timedelta(days=60)).isoformat()
    response = _change(alice, plan, "reschedule", {"remaining_count": 4,
                                                   "first_due_date": later})  # fmt: skip
    new = _ok(response)
    # #1 stays paid; the rest (7000 principal, 500 of it already paid towards #2) is re-split.
    assert [i["sequence"] for i in new["items"]] == [1, 2, 3, 4, 5]
    assert new["items"][1]["due_date"] == later
    assert [i["principal"]["amount"] for i in new["items"][1:]] == ["1750.00"] * 4
    assert new["items"][1]["covered"]["amount"] == "500.00"
    assert len(new["superseded"]) == 7
    assert new["installment_count"] == 5 and new["remaining"]["amount"] == "6500.00"
    # A stale version is refused.
    stale = _change(alice, plan, "reschedule", {"remaining_count": 2, "first_due_date": later})
    assert stale.status_code == 412


def test_removing_a_mistaken_plan_undoes_its_money_and_needs_no_payments(
    people: list[Person],
) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    plan = _plan(alice, bank)
    # The purchase and the debt start can't be voided on their own.
    purchase = _ok(alice.get(f"/transactions/{plan['purchase_transaction_id']}"))
    match = {**alice.h(False), "If-Match": f'"{purchase["version"]}"'}
    alone = alice.c.post(f"{API}/transactions/{purchase['id']}/void", headers=match)
    assert (alone.status_code, alone.json()["code"]) == (409, "managed_by_installment_plan")
    reprice = alice.c.patch(
        f"{API}/transactions/{purchase['id']}",
        json={"amount": {"amount": "9000.00", "currency": "EUR"}},
        headers={**alice.h(False), "If-Match": f'"{purchase["version"]}"'},
    )
    assert reprice.json()["code"] == "managed_by_installment_plan"
    removed = _ok(_change(alice, plan, "remove"))
    assert removed["status"] == "archived"
    assert _balance(alice, bank) == "20000.00"
    assert _summary(alice).get("expenses", {}).get("amount", "0.00") == "0.00"
    # With a payment, it can't be removed.
    second = _pay(alice, _plan(alice, bank), bank, principal="100.00")
    refused = _change(alice, second, "remove")
    assert (refused.status_code, refused.json()["code"]) == (409, "plan_has_payments")
    # Archiving needs a settled plan.
    early = _change(alice, second, "archive")
    assert (early.status_code, early.json()["code"]) == (409, "plan_not_settled")


def test_a_linked_goal_follows_the_plan_without_counting_money_twice(
    people: list[Person],
) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    target = {"amount": "2000.00", "currency": "EUR"}
    goal = _ok(alice.post("/goals", {"name": "Car paid early", "target": target}), 201)
    plan = _plan(alice, bank, goal_id=goal["id"])
    assert plan["goal_name"] == "Car paid early"
    _pay(alice, plan, bank, principal="1500.00")
    linked = _ok(alice.get(f"/goals/{goal['id']}"))
    assert (linked["progress"]["amount"], linked["plan_name"]) == ("1500.00", "Car")
    refused = alice.post(f"/goals/{goal['id']}/add", {"amount": "10.00"})
    assert (refused.status_code, refused.json()["code"]) == (409, "goal_follows_plan")
    # A goal in another currency, or one already linked, can't be linked.
    other = _plan(alice, bank, goal_id=goal["id"], status=409)
    assert other["code"] == "goal_already_linked"


def test_overview_shows_commitments_due_windows_and_the_income_share(
    people: list[Person],
) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    last_month = TODAY.replace(day=1) - timedelta(days=5)
    _ok(alice.post("/transactions", {
        "type": "income", "account_id": bank["id"],
        "amount": {"amount": "4000.00", "currency": "EUR"},
        "category_id": _category_id(alice, "salary"), "effective_date": last_month.isoformat(),
    }), 201)  # fmt: skip
    _plan(alice, bank, interest_per_installment="20.00")
    body = _ok(alice.get("/installments-overview"))
    eur = body["currencies"][0]
    assert eur["monthly"]["amount"] == "1020.00"
    assert eur["remaining_principal"]["amount"] == "8000.00"
    assert eur["scheduled_interest"]["amount"] == "160.00"
    assert eur["remaining_obligations"]["amount"] == "8160.00"
    assert (eur["average_income"]["amount"], eur["income_months"]) == ("4000.00", 1)
    assert eur["income_ratio_percent"] == "25.5"
    windows = {w["months"]: w["amount"]["amount"] for w in eur["due_within"]}
    assert windows[1] == "1020.00" and windows[12] == "8160.00"
    assert len(body["upcoming"]) == 8


def test_payments_are_idempotent_and_plans_are_private(people: list[Person]) -> None:
    alice, bob, *_ = people
    bank = _setup(alice, opening="20000.00")
    _setup(bob)
    plan = _plan(alice, bank)
    headers = alice.h()
    body = {"account_id": bank["id"], "principal": "1000.00"}
    first = alice.c.post(f"{API}/installments/{plan['id']}/payments", json=body, headers=headers)
    second = alice.c.post(f"{API}/installments/{plan['id']}/payments", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert len(_ok(alice.get(f"/installments/{plan['id']}"))["payments"]) == 1
    # Bob can't see or pay it.
    assert bob.get(f"/installments/{plan['id']}").status_code == 404
    assert bob.post(f"/installments/{plan['id']}/payments", body).status_code == 404
    assert _ok(bob.get("/installments")) == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"down_payment": "10000.00"}, "down_payment_too_large"),
        ({"item_price": None}, "price_required"),
        ({"category_id": None}, "category_required"),
        ({"installment_count": 9000}, "validation_error"),
        ({"currency": "USD"}, "account_currency_mismatch"),
    ],
)
def test_invalid_plans_are_refused_and_post_nothing(
    people: list[Person], body: dict[str, Any], code: str
) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    refused = _plan(alice, bank, status=422, **body)
    assert refused["code"] == code
    assert _balance(alice, bank) == "20000.00"
    assert _ok(alice.get("/installments")) == []


def test_interest_from_an_account_in_another_currency_is_refused(people: list[Person]) -> None:
    alice, *_ = people
    bank = _setup(alice, opening="20000.00")
    usd = _ok(alice.post("/accounts", {"name": "Dollars", "type": "bank", "currency": "USD"}), 201)
    plan = _plan(alice, bank, interest_per_installment="10.00")
    refused = alice.post(f"/installments/{plan['id']}/payments",
                         {"account_id": usd["id"], "principal": "100.00",
                          "principal_account_amount": "110.00", "interest": "10.00"})  # fmt: skip
    assert (refused.status_code, refused.json()["code"]) == (422, "interest_account_currency")
    # Principal alone from the other currency is fine (both real amounts entered).
    ok = _pay(alice, plan, usd, principal="100.00", principal_account_amount="110.00")
    assert ok["remaining"]["amount"] == "7900.00"
