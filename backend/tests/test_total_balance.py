"""Total Balance (FINANCIAL_RULES.md §7c): converted for display only, never stored, never combined
without a rate, and never touched by debts or installment plans."""

from typing import Any

from tests.test_fx import providers  # noqa: F401 - the fixture
from tests.test_installments import _plan
from tests.test_links import _balance, _setup
from tests.test_trips import Person, _ok, people  # noqa: F401 - fixtures

TOTAL = "/balances/total"


def _account(person: Person, currency: str, opening: str) -> dict[str, Any]:
    body = {"name": f"{currency} wallet", "type": "wallet", "currency": currency,
            "opening_balance": {"amount": opening, "currency": currency}}  # fmt: skip
    return dict(_ok(person.post("/accounts", body), 201))


def test_each_currency_is_converted_explicitly_and_debts_stay_out(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)  # 1000.00 EUR
    try_ = _account(alice, "TRY", "5631.00")  # 100.00 EUR at 56.31
    _plan(alice, eur, item_price="500.00", down_payment="100.00", installment_count=4)

    body = _ok(alice.get(TOTAL, target="EUR"))
    accounts = {a["currency"]: a for a in body["accounts"] if a["balance"]["amount"] != "0.00"}
    assert accounts["EUR"]["balance"]["amount"] == "900.00"
    assert accounts["TRY"]["equivalent"] == {"amount": "100.00", "currency": "EUR"}
    # The 400.00 still owed on the plan is not in here.
    assert body["balances"]["combined"]["amount"] == "1000.00"
    assert {r["currency"]: r["status"] for r in body["rates"]}["TRY"] == "ok"
    assert "net_worth" not in body and "liabilities" not in body

    # In TRY instead (the TRY->EUR reference is 0.017759): another view of the same balances.
    in_try = _ok(alice.get(TOTAL, target="TRY"))
    assert in_try["balances"]["combined"]["amount"] == "56309.53"  # 50678.53 + 5631.00
    assert _balance(alice, eur) == "900.00" and _balance(alice, try_) == "5631.00"


def test_net_worth_no_longer_exists(people: list[Person]) -> None:
    alice, *_ = people
    assert alice.get("/wealth", target="EUR").status_code == 404


def test_a_currency_without_a_rate_means_no_combined_figure(people: list[Person]) -> None:
    alice, *_ = people
    _setup(alice)
    _account(alice, "JPY", "1000")
    body = _ok(alice.get(TOTAL, target="EUR"))
    assert body["missing_rates"] == ["JPY"]
    assert body["balances"]["combined"] is None and body["balances"]["complete"] is False
    # Each currency is still shown as it is.
    assert {m["currency"] for m in body["balances"]["by_currency"]} == {"EUR", "JPY"}


def test_stale_rates_are_flagged(people: list[Person], providers: Any) -> None:
    alice, *_ = people
    _setup(alice)
    _account(alice, "TRY", "100.00")
    _ok(alice.get(TOTAL, target="EUR"))  # stores today's reference
    providers.mode = "down"
    gateway = alice.c.app.state.fx  # type: ignore[attr-defined]
    gateway.reset()
    body = _ok(alice.get(TOTAL, target="EUR"))
    # The provider is down: the stored rate is used, and flagged as a fallback (stale).
    assert body["is_stale"] is True
    assert body["balances"]["combined"] is not None


def test_total_balance_is_private(people: list[Person]) -> None:
    alice, bob, *_ = people
    _setup(alice, opening="777.00")
    _setup(bob, opening="5.00")
    body = _ok(bob.get(TOTAL, target="EUR"))
    assert body["balances"]["combined"]["amount"] == "5.00"
    assert all(a["balance"]["amount"] != "777.00" for a in body["accounts"])


def test_month_figures_list_every_category_and_received_converts(people: list[Person]) -> None:
    alice, *_ = people
    eur = _setup(alice)
    categories = _ok(alice.get("/categories"))
    expense = [c for c in categories if c["kind"] == "expense"]
    income = next(c for c in categories if c["kind"] == "income")
    for i, category in enumerate(expense[:7]):
        body = {"type": "expense", "account_id": eur["id"], "category_id": category["id"],
                "amount": {"amount": f"{10 + i}.00", "currency": "EUR"}}  # fmt: skip
        _ok(alice.post("/transactions", body), 201)
    pay = {"type": "income", "account_id": eur["id"], "category_id": income["id"],
           "amount": {"amount": "250.00", "currency": "EUR"}}  # fmt: skip
    _ok(alice.post("/transactions", pay), 201)

    row = _ok(alice.get("/summary"))["currencies"][0]
    assert len(row["categories"]) == 7 and len(row["top_categories"]) == 5
    assert row["categories"][0]["amount"]["amount"] == "16.00"  # the largest first
    assert row["uncategorized"] is None
    spent = sum(float(c["amount"]["amount"]) for c in row["categories"])
    assert spent == float(row["expenses"]["amount"])

    month = _ok(alice.get("/summary/equivalent", target="EUR"))
    assert month["combined"]["amount"] == row["expenses"]["amount"]
    assert month["received"]["combined"] == {"amount": "250.00", "currency": "EUR"}
    assert month["received"]["parts"][0]["spent"]["amount"] == "250.00"
