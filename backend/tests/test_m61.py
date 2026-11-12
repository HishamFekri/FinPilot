"""M6.1: budget currency rules, cross-surface reconciliation, export completeness and safety, and
Home's approximate spending equivalent.

Users sign in through the real Google routes against a fake Google; exchange rates come from the
fake providers in test_fx (MOCKED, tests only).
"""

import csv
import io
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ApiError
from app.db.models import Budget, JournalLine, Transaction
from app.services import exports
from tests.auth_harness import count, sign_in, write_headers
from tests.google_fakes import FakeGoogle
from tests.test_fx import TODAY, FakeProviders, providers  # noqa: F401 - the fixture

API = "/api/v1"
FIRST = TODAY.replace(day=1)
LAST_MONTH = (FIRST - timedelta(days=1)).replace(day=1)
MONTH = TODAY.strftime("%Y-%m")


def _keyed(csrf: str) -> dict[str, str]:
    return {**write_headers(csrf), "Idempotency-Key": str(uuid.uuid4())}


def _ok(response: Any) -> Any:
    assert response.status_code < 300, response.text
    return response.json() if response.content else None


class World:
    def __init__(self, client: TestClient, csrf: str) -> None:
        self.c = client
        self.csrf = csrf
        _ok(client.post(f"{API}/setup", json={"currency": "TRY"}, headers=write_headers(csrf)))
        accounts = {a["system_key"]: a for a in client.get(f"{API}/accounts").json()}
        self.bank, self.cash = accounts["bank"], accounts["cash"]
        self.cats = {c["system_key"]: c["id"] for c in client.get(f"{API}/categories").json()}

    def account(self, currency: str) -> dict[str, Any]:
        return dict(
            _ok(
                self.c.post(
                    f"{API}/accounts",
                    json={"name": f"Wallet {currency}", "type": "wallet", "currency": currency},
                    headers=_keyed(self.csrf),
                )
            )
        )

    def record(
        self,
        account: dict[str, Any],
        kind: str,
        amount: str,
        currency: str,
        category: str,
        on: date = TODAY,
        description: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        return dict(
            _ok(
                self.c.post(
                    f"{API}/transactions",
                    json={
                        "type": kind,
                        "account_id": account["id"],
                        "amount": {"amount": amount, "currency": currency},
                        "category_id": self.cats[category],
                        "effective_date": on.isoformat(),
                        "description": description or None,
                        **extra,
                    },
                    headers=_keyed(self.csrf),
                )
            )
        )

    def budget(self, category: str | None, amount: str, currency: str = "TRY") -> None:
        _ok(
            self.c.put(
                f"{API}/budgets",
                json={
                    "category_id": self.cats[category] if category else None,
                    "limit": {"amount": amount, "currency": currency},
                    "from_month": MONTH,
                },
                headers=write_headers(self.csrf),
            )
        )

    def budgets(self, month: str = MONTH) -> dict[tuple[str | None, str], dict[str, Any]]:
        body = self.c.get(f"{API}/budgets", params={"month": month}).json()
        return {
            (b["category"]["system_key"] if b["category"] else None, b["currency"]): b
            for b in body["budgets"]
        }

    def detail(self, category: str, currency: str = "TRY", month: str = MONTH) -> Any:
        return self.c.get(
            f"{API}/budgets/detail",
            params={"category_id": self.cats[category], "currency": currency, "month": month},
        ).json()


@pytest.fixture
def world(browser: TestClient, google: FakeGoogle, providers: FakeProviders) -> World:
    return World(browser, sign_in(browser, google))


EUR_22 = {"method": "actual_amount", "account_amount": "1231.91"}


# --- 2. Budgets and currencies


def test_foreign_purchase_counts_once_as_its_posted_amount(world: World) -> None:
    """Cases A and B: EUR 22 charged as TRY 1,231.91 counts once, in TRY, in Education."""
    world.budget("education", "5000.00")
    world.record(world.bank, "expense", "300.00", "TRY", "education", description="Books")
    posted = world.record(
        world.bank, "expense", "22.00", "EUR", "education", description="Course", conversion=EUR_22
    )
    assert (posted["amount"], posted["original_amount"]) == (
        {"amount": "1231.91", "currency": "TRY"},
        {"amount": "22.00", "currency": "EUR"},
    )
    budget = world.budgets()[("education", "TRY")]
    assert budget["spent"]["amount"] == "1531.91"  # 300 + 1,231.91; the EUR 22 isn't added
    assert budget["not_counted"] == []
    detail = world.detail("education")
    assert sorted(t["amount"]["amount"] for t in detail["transactions"]) == ["1231.91", "300.00"]
    assert detail["excluded"] == []
    # No other surface sees a EUR expense either.
    report = world.c.get(f"{API}/reports").json()
    assert [c["currency"] for c in report["by_currency"]] == ["TRY"]


def test_other_account_currency_is_listed_not_counted(world: World) -> None:
    """Case C: a USD account's Education expense isn't added to the TRY budget (OD-7)."""
    world.budget("education", "5000.00")
    usd = world.account("USD")
    world.record(world.bank, "expense", "100.00", "TRY", "education")
    world.record(usd, "expense", "50.00", "USD", "education", description="Online course")
    budget = world.budgets()[("education", "TRY")]
    assert budget["spent"]["amount"] == "100.00"
    assert budget["not_counted"] == [{"amount": "50.00", "currency": "USD"}]
    excluded = world.detail("education")["excluded"]
    assert [(t["description"], t["amount"]["currency"]) for t in excluded] == [
        ("Online course", "USD")
    ]


def test_historical_amounts_never_revalue(world: World, providers: FakeProviders) -> None:
    """Case D: today's rate changing leaves posted spending alone."""
    world.budget("education", "5000.00")
    rate_id = world.c.get(f"{API}/fx/convert", params={"from": "EUR", "to": "TRY"}).json()["rate"][
        "id"
    ]
    world.record(
        world.bank,
        "expense",
        "22.00",
        "EUR",
        "education",
        conversion={"method": "reference", "rate_id": rate_id},
    )
    before = world.budgets()[("education", "TRY")]["spent"]["amount"]
    assert before == "1238.82"  # 22 x 56.31
    providers.rates[("EUR", "TRY")] = "70.00"
    world.c.get(f"{API}/fx/convert", params={"from": "EUR", "to": "TRY"})
    assert world.budgets()[("education", "TRY")]["spent"]["amount"] == before


def test_corrected_foreign_expense_counts_once_in_its_new_month(world: World) -> None:
    """Case E: moving it to last month moves its posted amount there; voiding removes it."""
    world.budget("education", "5000.00")
    _ok(
        world.c.put(
            f"{API}/budgets",
            json={
                "category_id": world.cats["education"],
                "limit": {"amount": "5000.00", "currency": "TRY"},
                "from_month": LAST_MONTH.strftime("%Y-%m"),
            },
            headers=write_headers(world.csrf),
        )
    )
    posted = world.record(world.bank, "expense", "22.00", "EUR", "education", conversion=EUR_22)
    moved = _ok(
        world.c.patch(
            f"{API}/transactions/{posted['id']}",
            json={"effective_date": LAST_MONTH.isoformat()},
            headers={**write_headers(world.csrf), "If-Match": f'"{posted["version"]}"'},
        )
    )
    assert moved["amount"]["amount"] == "1231.91"
    this_month = world.budgets()[("education", "TRY")]["spent"]["amount"]
    last_month = world.budgets(LAST_MONTH.strftime("%Y-%m"))[("education", "TRY")]
    assert (this_month, last_month["spent"]["amount"]) == ("0.00", "1231.91")
    _ok(
        world.c.post(
            f"{API}/transactions/{posted['id']}/void",
            headers={**write_headers(world.csrf), "If-Match": f'"{moved["version"]}"'},
        )
    )
    last_month = world.budgets(LAST_MONTH.strftime("%Y-%m"))[("education", "TRY")]
    assert last_month["spent"]["amount"] == "0.00"
    assert world.detail("education", month=LAST_MONTH.strftime("%Y-%m"))["transactions"] == []


def test_only_real_expenses_count(world: World) -> None:
    """Income, transfers, opening balances, adjustments, debt principal and expected recurring
    amounts never count toward an overall budget."""
    world.budget(None, "100000.00")
    world.record(world.bank, "income", "40000.00", "TRY", "salary")
    world.record(world.bank, "expense", "750.00", "TRY", "groceries")
    _ok(
        world.c.post(
            f"{API}/accounts",
            json={
                "name": "Savings",
                "type": "savings",
                "currency": "TRY",
                "opening_balance": {"amount": "9000.00", "currency": "TRY"},
            },
            headers=_keyed(world.csrf),
        )
    )
    _ok(
        world.c.post(
            f"{API}/transfers",
            json={
                "from_account_id": world.bank["id"],
                "to_account_id": world.cash["id"],
                "amount": {"amount": "1000.00", "currency": "TRY"},
            },
            headers=_keyed(world.csrf),
        )
    )
    _ok(
        world.c.post(
            f"{API}/accounts/{world.cash['id']}/adjustments",
            json={
                "target_balance": {"amount": "900.00", "currency": "TRY"},
                "expected_balance": {"amount": "1000.00", "currency": "TRY"},
            },
            headers=_keyed(world.csrf),
        )
    )
    debt = _ok(
        world.c.post(
            f"{API}/debts",
            json={
                "direction": "owed_to_me",
                "counterparty": "Deniz",
                "amount": {"amount": "2000.00", "currency": "TRY"},
                "account_id": world.bank["id"],
            },
            headers=_keyed(world.csrf),
        )
    )
    assert debt is not None
    _ok(
        world.c.post(
            f"{API}/recurring/rules",
            json={
                "kind": "expense",
                "name": "Rent",
                "currency": "TRY",
                "amount": {"amount": "17500.00", "currency": "TRY"},
                "account_id": world.bank["id"],
                "category_id": world.cats["housing"],
                "frequency": "monthly",
                "window_start_day": 1,
                "window_end_day": 28,
                "start_date": FIRST.isoformat(),
            },
            headers=_keyed(world.csrf),
        )
    )
    world.c.get(f"{API}/recurring/occurrences")  # generates the expected occurrence
    assert world.budgets()[(None, "TRY")]["spent"]["amount"] == "750.00"


# --- 3. The same figures everywhere


def test_every_surface_reconciles(
    world: World, monkeypatch: pytest.MonkeyPatch, auth_app: FastAPI
) -> None:
    world.budget(None, "100000.00")
    world.record(world.bank, "income", "40000.00", "TRY", "salary")
    world.record(world.bank, "expense", "750.00", "TRY", "groceries")
    world.record(world.cash, "expense", "120.50", "TRY", "food_and_drinks")
    world.record(world.bank, "expense", "22.00", "EUR", "education", conversion=EUR_22)
    voided = world.record(world.bank, "expense", "999.00", "TRY", "shopping")
    _ok(
        world.c.post(
            f"{API}/transactions/{voided['id']}/void",
            headers={**write_headers(world.csrf), "If-Match": f'"{voided["version"]}"'},
        )
    )
    expected = "2102.41"  # 750 + 120.50 + 1,231.91; the voided 999 is gone
    home = world.c.get(f"{API}/summary", params={"month": MONTH}).json()["currencies"][0]
    window = {"from": FIRST.isoformat(), "to": TODAY.isoformat()}
    insights = world.c.get(f"{API}/insights", params=window).json()
    report = world.c.get(f"{API}/reports", params=window).json()["by_currency"][0]
    assert home["expenses"]["amount"] == expected
    assert world.budgets()[(None, "TRY")]["spent"]["amount"] == expected
    assert insights["totals"]["expenses"]["amount"] == expected
    assert report["totals"]["expenses"]["amount"] == expected
    assert report["totals"]["income"]["amount"] == insights["totals"]["income"]["amount"]
    assert report["totals"]["net"]["amount"] == insights["totals"]["net"]["amount"] == "37897.59"
    # CSV: the expense rows add up to the same, in the posted currency.
    rows = list(
        csv.DictReader(
            io.StringIO(
                world.c.get(f"{API}/reports/export.csv", params=window).content.decode("utf-8-sig")
            )
        )
    )
    spent = sum(-Decimal(r["amount"]) for r in rows if r["type"] == "Expense")
    assert spent == Decimal(expected)
    assert {r["currency"] for r in rows} == {"TRY"}
    # PDF: the same report object fills the template.
    captured: dict[str, str] = {}

    def fake_pdf(html: str, *_: Any) -> bytes:
        captured["html"] = html
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(exports, "render_pdf", fake_pdf)
    assert world.c.get(f"{API}/reports/export.pdf", params=window).status_code == 200
    assert "2,102.41 TRY" in captured["html"]
    assert "40,000.00 TRY" in captured["html"]


# --- 4 and 5. Print and PDF never look complete when rows were left out


def test_pdf_declares_partial_output(
    world: World, monkeypatch: pytest.MonkeyPatch, auth_app: FastAPI
) -> None:
    auth_app.state.settings = auth_app.state.settings.model_copy(update={"pdf_max_rows": 2})
    for amount in ("10.00", "20.00", "30.00"):
        world.record(world.bank, "expense", amount, "TRY", "groceries")
    report = world.c.get(f"{API}/reports").json()
    assert (report["table_count"], report["limits"]["pdf_rows"]) == (3, 2)
    captured: dict[str, str] = {}

    def fake_pdf(html: str, *_: Any) -> bytes:
        captured["html"] = html
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(exports, "render_pdf", fake_pdf)
    response = world.c.get(f"{API}/reports/export.pdf")
    assert response.headers["X-Report-Complete"] == "false"
    assert (
        response.headers["X-Report-Rows-Included"],
        response.headers["X-Report-Rows-Total"],
    ) == ("2", "3")
    assert "PARTIAL REPORT" in captured["html"]
    assert "Lists 2 of 3 matching transactions; 1 are left out" in captured["html"]
    auth_app.state.settings = auth_app.state.settings.model_copy(update={"pdf_max_rows": 2000})
    complete = world.c.get(f"{API}/reports/export.pdf")
    assert complete.headers["X-Report-Complete"] == "true"
    assert "Complete report: all 3 matching transactions are listed." in captured["html"]
    assert "PARTIAL REPORT" not in captured["html"]


def test_description_search_counts_the_table_only(world: World) -> None:
    world.record(world.bank, "expense", "10.00", "TRY", "groceries", description="Migros")
    world.record(world.bank, "expense", "20.00", "TRY", "groceries", description="BİM")
    body = world.c.get(f"{API}/reports", params={"q": "migros"}).json()
    assert (body["transaction_count"], body["table_count"]) == (2, 1)
    assert body["by_currency"][0]["totals"]["expenses"]["amount"] == "30.00"


# --- 7. CSV


def test_csv_escapes_commas_quotes_and_newlines(world: World) -> None:
    tricky = 'Dinner, "the good place"'
    world.record(world.bank, "expense", "10.00", "TRY", "groceries", description=tricky)
    world.record(world.bank, "expense", "5.00", "TRY", "groceries", description="+90 phone")
    body = world.c.get(f"{API}/reports/export.csv").content.decode("utf-8-sig")
    rows = {r["description"]: r for r in csv.DictReader(io.StringIO(body))}
    assert tricky in rows
    assert "'+90 phone" in rows  # neutralized
    assert rows[tricky]["amount"] == "-10.00"


# --- 6. The PDF renderer cleans up and stays bounded


class _FakeBrowser:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.closed = False
        self.launch_options: dict[str, Any] = {}

    def new_context(self, **options: Any) -> "_FakeBrowser":
        assert options == {"java_script_enabled": False, "offline": True}
        return self

    def set_default_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    def new_page(self) -> "_FakeBrowser":
        return self

    def route(self, pattern: str, handler: Any) -> None:
        assert pattern == "**/*"

    def set_content(self, html: str, **_: Any) -> None:
        self.html = html

    def pdf(self, **_: Any) -> bytes:
        from playwright.sync_api import Error

        if self.fail:
            raise Error("simulated crash")
        return b"%PDF-1.7"

    def close(self) -> None:
        self.closed = True


def _fake_playwright(monkeypatch: pytest.MonkeyPatch, browser: _FakeBrowser) -> None:
    import playwright.sync_api

    class Launcher:
        def launch(self, **options: Any) -> _FakeBrowser:
            browser.launch_options = options
            return browser

    class Session:
        chromium = Launcher()

        def __enter__(self) -> "Session":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: Session())


SETTINGS = exports.PdfSettings(channel=None, executable=None, timeout_ms=5000, max_concurrent=1)
FOOTER = {"dir": "ltr", "generated": "<b>x</b>", "page": "Page", "of": "of"}


def test_pdf_browser_is_closed_and_slot_released_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = _FakeBrowser(fail=True)
    _fake_playwright(monkeypatch, browser)
    with pytest.raises(ApiError) as error:
        exports.render_pdf("<p>hi</p>", SETTINGS, FOOTER)
    assert error.value.code == "pdf_unavailable"
    assert browser.closed
    # The slot came back: the next render runs.
    ok = _FakeBrowser(fail=False)
    _fake_playwright(monkeypatch, ok)
    assert exports.render_pdf("<p>hi</p>", SETTINGS, FOOTER) == b"%PDF-1.7"
    assert ok.closed and ok.timeout == 5000
    # Playwright would otherwise pass --no-sandbox.
    assert ok.launch_options["chromium_sandbox"] is True


def test_pdf_concurrency_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_playwright(monkeypatch, _FakeBrowser(fail=False))
    slots = exports._slots(1)
    assert slots.acquire(timeout=1)
    try:
        with pytest.raises(ApiError) as error:
            exports.render_pdf("<p>hi</p>", SETTINGS, FOOTER)
        assert error.value.code == "pdf_busy"
    finally:
        slots.release()


def test_report_html_escapes_user_text() -> None:
    html = exports.render_html(
        {
            "lang": "en",
            "dir": "ltr",
            "t": {},
            "rows": [{"description": "<img src=x onerror=alert(1)>", "date": "2026-11-07"}],
            "currencies": [],
            "complete": True,
            "completeness": "",
        }
    )
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


# --- Home: approximate spending in another currency


def _equivalent(world: World, target: str, **params: Any) -> Any:
    return world.c.get(f"{API}/summary/equivalent", params={"target": target, **params})


def test_single_currency_equivalent_uses_todays_reference(world: World) -> None:
    world.record(world.bank, "expense", "1000.00", "TRY", "groceries")
    body = _ok(_equivalent(world, "USD"))
    (part,) = body["parts"]
    exact = (Decimal("1000.00") / Decimal("50.153")).quantize(Decimal("0.01"), ROUND_HALF_UP)
    assert part["spent"] == {"amount": "1000.00", "currency": "TRY"}  # untouched
    assert (
        part["equivalent"]
        == {"amount": str(exact), "currency": "USD"}
        == {
            "amount": "19.94",
            "currency": "USD",
        }
    )
    assert (part["rate"], part["status"], part["is_stale"]) == ("50.153", "ok", False)
    assert part["provider"] and part["retrieved_at"] and part["rate_date"]
    assert body["combined"] == part["equivalent"] and body["complete"]


def test_multi_currency_equivalent_keeps_subtotals_and_sums_exactly(world: World) -> None:
    eur = world.account("EUR")
    world.record(world.bank, "expense", "1000.00", "TRY", "groceries")
    world.record(eur, "expense", "10.00", "EUR", "groceries")
    body = _ok(_equivalent(world, "USD"))
    parts = {p["currency"]: p for p in body["parts"]}
    assert parts["TRY"]["spent"]["amount"] == "1000.00"
    assert parts["EUR"]["spent"]["amount"] == "10.00"
    assert parts["EUR"]["equivalent"]["amount"] == "11.23"  # 10 / 0.89075 = 11.2265
    assert body["combined"] == {"amount": "31.17", "currency": "USD"}  # 19.94 + 11.23
    # Switching the target: the euro part is already in euros.
    body = _ok(_equivalent(world, "EUR"))
    parts = {p["currency"]: p for p in body["parts"]}
    assert parts["EUR"]["status"] == "same"
    assert parts["EUR"]["equivalent"]["amount"] == "10.00"
    assert parts["TRY"]["equivalent"]["amount"] == "17.76"  # 1000 / 56.31
    assert body["combined"]["amount"] == "27.76"


def test_missing_rate_means_no_combined_total(world: World) -> None:
    usd = world.account("USD")
    world.record(world.bank, "expense", "1000.00", "TRY", "groceries")
    world.record(usd, "expense", "5.00", "USD", "groceries")
    body = _ok(_equivalent(world, "JPY"))  # the fake has USD→JPY only, not JPY→TRY
    parts = {p["currency"]: p for p in body["parts"]}
    assert parts["TRY"]["status"] == "unavailable"
    assert parts["TRY"]["equivalent"] is None
    assert parts["TRY"]["reason"] in {"fx_pair_unsupported", "fx_unavailable"}
    assert body["combined"] is None and body["complete"] is False


def test_provider_failure_never_invents_a_rate(
    world: World,
    providers: FakeProviders,
    auth_app: FastAPI,
) -> None:
    world.record(world.bank, "expense", "1000.00", "TRY", "groceries")
    providers.mode = "down"
    body = _ok(_equivalent(world, "USD"))
    assert body["parts"][0]["status"] == "unavailable"
    assert body["combined"] is None
    # Once a rate is stored, an outage falls back to it, marked stale.
    providers.mode = "ok"
    auth_app.state.fx = providers.gateway()
    _ok(_equivalent(world, "USD"))
    providers.mode = "down"
    auth_app.state.fx = providers.gateway()
    body = _ok(_equivalent(world, "USD"))
    assert body["parts"][0]["status"] == "ok"
    assert body["parts"][0]["is_stale"] is True and body["is_stale"] is True


def test_refresh_asks_again_and_updates(world: World, providers: FakeProviders) -> None:
    """A day's reference is stored once and never changes; Refresh picks up a newly published
    day without waiting for the half-hour answer cache."""
    world.record(world.bank, "expense", "1000.00", "TRY", "groceries")
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    providers.shift[TODAY.isoformat()] = yesterday  # today's isn't published yet
    first = _ok(_equivalent(world, "USD"))
    assert first["combined"]["amount"] == "19.94"
    assert first["parts"][0]["rate_date"] == yesterday
    # The provider publishes today's reference at a new rate.
    providers.shift.clear()
    providers.rates[("USD", "TRY")] = "40.00"
    assert _ok(_equivalent(world, "USD"))["combined"]["amount"] == "19.94"  # cached answer
    refreshed = _ok(_equivalent(world, "USD", refresh="true"))
    assert refreshed["combined"]["amount"] == "25.00"
    assert refreshed["parts"][0]["rate_date"] == TODAY.isoformat()


def test_equivalent_changes_no_financial_record(
    world: World, factory: "sessionmaker[Session]"
) -> None:
    world.budget(None, "5000.00")
    world.record(world.bank, "expense", "1000.00", "TRY", "groceries")
    before = [count(factory, model) for model in (Transaction, JournalLine, Budget)]
    balance = world.c.get(f"{API}/accounts/{world.bank['id']}").json()["balance"]
    for target in ("USD", "EUR", "GBP", "TRY"):
        _equivalent(world, target)
    _equivalent(world, "USD", refresh="true")
    assert [count(factory, model) for model in (Transaction, JournalLine, Budget)] == before
    assert world.c.get(f"{API}/accounts/{world.bank['id']}").json()["balance"] == balance
    assert world.c.get(f"{API}/summary").json()["currencies"][0]["expenses"]["amount"] == (
        "1000.00"
    )


def test_equivalent_validates_target(world: World) -> None:
    assert _equivalent(world, "usd").status_code == 422
    assert _equivalent(world, "ZZZ").status_code == 422
    assert _ok(_equivalent(world, "USD"))["parts"] == []  # nothing spent yet
