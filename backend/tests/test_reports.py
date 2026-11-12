"""M6: budgets, insights, reports, and CSV/PDF exports, all from the same ledger queries.

Users sign in through the real Google routes against a fake Google (MOCKED, tests only).
"""

import csv
import io
import json
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.report_text import CATEGORY_KEYS, TEXT
from tests.auth_harness import sign_in, write_headers
from tests.google_fakes import FakeGoogle

API = "/api/v1"
TODAY = datetime.now(UTC).date()  # test users live in UTC
MONTH = TODAY.strftime("%Y-%m")
FIRST = TODAY.replace(day=1)
LAST_MONTH = (FIRST - timedelta(days=1)).replace(day=1)
NEXT_MONTH = (FIRST + timedelta(days=32)).replace(day=1)


def _keyed(csrf: str) -> dict[str, str]:
    return {**write_headers(csrf), "Idempotency-Key": str(uuid.uuid4())}


def _setup(client: TestClient, csrf: str) -> dict[str, dict[str, Any]]:
    assert (
        client.post(
            f"{API}/setup", json={"currency": "TRY"}, headers=write_headers(csrf)
        ).status_code
        == 200
    )
    return {a["system_key"]: a for a in client.get(f"{API}/accounts").json()}


def _cat(client: TestClient, key: str) -> str:
    return str(
        next(c["id"] for c in client.get(f"{API}/categories").json() if c["system_key"] == key)
    )


def _record(
    client: TestClient,
    csrf: str,
    account: dict[str, Any],
    kind: str,
    amount: str,
    category: str,
    on: date = TODAY,
    description: str | None = None,
    currency: str = "TRY",
) -> dict[str, Any]:
    response = client.post(
        f"{API}/transactions",
        json={
            "type": kind,
            "account_id": account["id"],
            "amount": {"amount": amount, "currency": currency},
            "category_id": _cat(client, category),
            "effective_date": on.isoformat(),
            "description": description,
        },
        headers=_keyed(csrf),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _budget(client: TestClient, csrf: str, amount: str, category: str | None, **extra: Any) -> Any:
    body = {
        "category_id": _cat(client, category) if category else None,
        "limit": {"amount": amount, "currency": "TRY"},
        "from_month": MONTH,
        **extra,
    }
    return client.put(f"{API}/budgets", json=body, headers=write_headers(csrf))


def _by_key(items: list[dict[str, Any]], key: str | None) -> dict[str, Any]:
    return next(b for b in items if (b["category"]["system_key"] if b["category"] else None) == key)


@pytest.fixture
def world(browser: TestClient, google: FakeGoogle) -> tuple[TestClient, str, dict[str, Any]]:
    """Salary, groceries, food, a transfer, an adjustment, and a debt. Only expenses spend."""
    csrf = sign_in(browser, google)
    accounts = _setup(browser, csrf)
    bank, cash = accounts["bank"], accounts["cash"]
    _record(browser, csrf, bank, "income", "35000.00", "salary", description="Salary")
    _record(browser, csrf, bank, "expense", "1200.00", "groceries", description="Weekly shop")
    _record(browser, csrf, cash, "expense", "300.00", "food_and_drinks", description="Lunch")
    _record(browser, csrf, cash, "expense", "100.00", "food_and_drinks", description="Coffee")
    assert (
        browser.post(
            f"{API}/transfers",
            json={
                "from_account_id": bank["id"],
                "to_account_id": cash["id"],
                "amount": {"amount": "2000.00", "currency": "TRY"},
            },
            headers=_keyed(csrf),
        ).status_code
        == 201
    )
    assert (
        browser.post(
            f"{API}/accounts/{cash['id']}/adjustments",
            json={
                "target_balance": {"amount": "1500.00", "currency": "TRY"},
                "expected_balance": {"amount": "1600.00", "currency": "TRY"},
            },
            headers=_keyed(csrf),
        ).status_code
        == 201
    )
    assert (
        browser.post(
            f"{API}/debts",
            json={
                "direction": "i_owe",
                "counterparty": "Ahmet",
                "amount": {"amount": "5000.00", "currency": "TRY"},
                "account_id": bank["id"],
            },
            headers=_keyed(csrf),
        ).status_code
        == 201
    )
    return browser, csrf, {"bank": bank, "cash": cash}


# --- Budgets


def test_budgets_count_only_real_spending(world: tuple[TestClient, str, dict[str, Any]]) -> None:
    client, csrf, _ = world
    assert _budget(client, csrf, "1500.00", None).status_code == 200
    assert _budget(client, csrf, "450.00", "food_and_drinks").status_code == 200
    assert _budget(client, csrf, "1000.00", "groceries", alert_percent=90).status_code == 200
    items = client.get(f"{API}/budgets", params={"month": MONTH}).json()["budgets"]
    overall = _by_key(items, None)
    # 1,200 + 300 + 100: not the salary, the transfer, the adjustment, or the borrowed money.
    assert (overall["spent"]["amount"], overall["remaining"]["amount"]) == ("1600.00", "-100.00")
    assert (overall["percent"], overall["state"]) == ("106.7", "exceeded")
    food = _by_key(items, "food_and_drinks")
    assert (food["spent"]["amount"], food["percent"], food["state"]) == (
        "400.00",
        "88.9",
        "approaching",
    )
    groceries = _by_key(items, "groceries")
    assert (groceries["state"], groceries["alert_percent"]) == ("exceeded", 90)
    assert items[0]["category"] is None  # overall first


def test_limits_are_effective_dated_and_archiving_keeps_the_past(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, csrf, _ = world
    _budget(client, csrf, "500.00", "food_and_drinks")
    later_limit = _budget(
        client, csrf, "800.00", "food_and_drinks", from_month=NEXT_MONTH.strftime("%Y-%m")
    )
    assert later_limit.status_code == 200, later_limit.text
    now = _by_key(
        client.get(f"{API}/budgets", params={"month": MONTH}).json()["budgets"], "food_and_drinks"
    )
    later = _by_key(
        client.get(f"{API}/budgets", params={"month": NEXT_MONTH.strftime("%Y-%m")}).json()[
            "budgets"
        ],
        "food_and_drinks",
    )
    assert (now["limit"]["amount"], later["limit"]["amount"]) == ("500.00", "800.00")
    assert (
        client.get(f"{API}/budgets", params={"month": LAST_MONTH.strftime("%Y-%m")}).json()[
            "budgets"
        ]
        == []
    )
    ended = client.post(
        f"{API}/budgets/end",
        json={
            "category_id": _cat(client, "food_and_drinks"),
            "currency": "TRY",
            "from_month": NEXT_MONTH.strftime("%Y-%m"),
        },
        headers=write_headers(csrf),
    ).json()
    assert ended["budgets"] == []
    # Ending from next month replaces next month's own row, so the last limit before it is 500.
    assert ended["archived"][0]["last_limit"]["amount"] == "500.00"
    # This month still shows it.
    assert client.get(f"{API}/budgets", params={"month": MONTH}).json()["budgets"]
    detail = client.get(
        f"{API}/budgets/detail",
        params={"category_id": _cat(client, "food_and_drinks"), "currency": "TRY", "month": MONTH},
    ).json()
    assert [r["limit"]["amount"] if r["limit"] else None for r in detail["history"]] == [
        "500.00",
        None,
    ]
    assert sorted(t["description"] for t in detail["transactions"]) == ["Coffee", "Lunch"]
    assert detail["months"][-1]["spent"]["amount"] == "400.00"


def test_corrections_and_voids_change_the_affected_month(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, csrf, accounts = world
    _budget(client, csrf, "1000.00", "food_and_drinks")
    old = _record(client, csrf, accounts["cash"], "expense", "50.00", "food_and_drinks", on=FIRST)
    moved = client.patch(
        f"{API}/transactions/{old['id']}",
        json={
            "effective_date": (FIRST - timedelta(days=1)).isoformat(),
            "amount": {"amount": "70.00", "currency": "TRY"},
        },
        headers={**write_headers(csrf), "If-Match": f'"{old["version"]}"'},
    )
    assert moved.status_code == 200
    food = _by_key(
        client.get(f"{API}/budgets", params={"month": MONTH}).json()["budgets"], "food_and_drinks"
    )
    assert food["spent"]["amount"] == "400.00"  # moved out of this month
    lunch = next(
        t for t in client.get(f"{API}/transactions", params={"q": "Lunch"}).json()["items"]
    )
    client.post(
        f"{API}/transactions/{lunch['id']}/void",
        headers={**write_headers(csrf), "If-Match": f'"{lunch["version"]}"'},
    )
    food = _by_key(
        client.get(f"{API}/budgets", params={"month": MONTH}).json()["budgets"], "food_and_drinks"
    )
    assert food["spent"]["amount"] == "100.00"


def test_other_currencies_are_listed_not_counted(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, csrf, _ = world
    usd = client.post(
        f"{API}/accounts",
        json={"name": "Dollar card", "type": "wallet", "currency": "USD"},
        headers=_keyed(csrf),
    ).json()
    _record(client, csrf, usd, "expense", "12.00", "food_and_drinks", currency="USD")
    _budget(client, csrf, "1000.00", "food_and_drinks")
    food = _by_key(
        client.get(f"{API}/budgets", params={"month": MONTH}).json()["budgets"], "food_and_drinks"
    )
    assert food["spent"]["amount"] == "400.00"
    assert food["not_counted"] == [{"amount": "12.00", "currency": "USD"}]


def test_budget_validation_and_isolation(
    auth_app: FastAPI, world: tuple[TestClient, str, dict[str, Any]], google: FakeGoogle
) -> None:
    client, csrf, _ = world
    assert _budget(client, csrf, "100.00", "salary").json()["code"] == "category_kind_mismatch"
    assert _budget(client, csrf, "0", None).json()["code"] == "amount_not_positive"
    assert _budget(client, csrf, "100.00", None, from_month="2026-13").status_code == 422
    assert _budget(client, csrf, "100.00", None, alert_percent=0).status_code == 422
    assert (
        client.put(
            f"{API}/budgets",
            json={"limit": {"amount": "1", "currency": "TRY"}, "from_month": MONTH},
        ).status_code
        == 403
    )
    food = _cat(client, "food_and_drinks")
    _budget(client, csrf, "100.00", "food_and_drinks")
    with TestClient(auth_app) as other:
        other_csrf = sign_in(other, google, subject="g-other", email="other@example.com")
        _setup(other, other_csrf)
        assert other.get(f"{API}/budgets").json()["budgets"] == []
        stolen = other.put(
            f"{API}/budgets",
            json={
                "category_id": food,
                "limit": {"amount": "1", "currency": "TRY"},
                "from_month": MONTH,
            },
            headers=write_headers(other_csrf),
        )
        assert stolen.status_code == 404
        detail = other.get(
            f"{API}/budgets/detail", params={"category_id": food, "currency": "TRY", "month": MONTH}
        )
        assert detail.status_code == 404


# --- Insights


def test_insights_use_spending_only_and_never_mix_currencies(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, _, accounts = world
    body = client.get(
        f"{API}/insights", params={"from": FIRST.isoformat(), "to": TODAY.isoformat()}
    ).json()
    assert body["currency"] == "TRY"
    assert (body["totals"]["income"]["amount"], body["totals"]["expenses"]["amount"]) == (
        "35000.00",
        "1600.00",
    )
    assert body["totals"]["net"]["amount"] == "33400.00"
    shares = [
        (c["category"]["system_key"], c["amount"]["amount"], c["percent"])
        for c in body["categories"]
    ]
    assert shares == [("groceries", "1200.00", "75.0"), ("food_and_drinks", "400.00", "25.0")]
    assert body["monthly"][-1]["expenses"]["amount"] == "1600.00"
    cash_only = client.get(
        f"{API}/insights",
        params={
            "from": FIRST.isoformat(),
            "to": TODAY.isoformat(),
            "account_id": accounts["cash"]["id"],
        },
    ).json()
    assert cash_only["totals"]["expenses"]["amount"] == "400.00"
    assert cash_only["totals"]["income"]["amount"] == "0.00"


# --- Reports


def test_report_totals_reconcile_with_balances(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, _, accounts = world
    body = client.get(
        f"{API}/reports", params={"from": FIRST.isoformat(), "to": TODAY.isoformat()}
    ).json()
    assert body["balances_applicable"] is True
    try_ = next(c for c in body["by_currency"] if c["currency"] == "TRY")
    b = try_["balances"]
    assert b["reconciles"] is True
    assert (b["income"]["amount"], b["expenses"]["amount"]) == ("35000.00", "1600.00")
    assert (b["debts_in"]["amount"], b["debts_out"]["amount"]) == ("5000.00", "0.00")
    # A transfer between two of the person's accounts nets out in the whole-portfolio view.
    assert (b["transfers_in"]["amount"], b["transfers_out"]["amount"]) == ("0.00", "0.00")
    assert b["adjustments"]["amount"] == "-100.00"
    total = sum(
        float(client.get(f"{API}/accounts/{a['id']}").json()["balance"]["amount"])
        for a in accounts.values()
    )
    assert float(b["closing"]["amount"]) == pytest.approx(total)
    # One account: the transfer appears, and it still reconciles.
    cash = client.get(
        f"{API}/reports",
        params={
            "from": FIRST.isoformat(),
            "to": TODAY.isoformat(),
            "account_id": accounts["cash"]["id"],
        },
    ).json()["by_currency"][0]["balances"]
    assert (cash["transfers_in"]["amount"], cash["reconciles"]) == ("2000.00", True)


def test_home_insights_and_reports_agree(world: tuple[TestClient, str, dict[str, Any]]) -> None:
    client, _, _ = world
    home = client.get(f"{API}/summary", params={"month": MONTH}).json()["currencies"][0]
    first, last = FIRST.isoformat(), TODAY.isoformat()
    insights = client.get(f"{API}/insights", params={"from": first, "to": last}).json()["totals"]
    report = client.get(f"{API}/reports", params={"from": first, "to": last}).json()["by_currency"][
        0
    ]["totals"]
    assert home["expenses"] == insights["expenses"] == report["expenses"]
    assert home["income"] == insights["income"] == report["income"]


def test_a_category_filter_hides_balances(world: tuple[TestClient, str, dict[str, Any]]) -> None:
    client, _, _ = world
    body = client.get(
        f"{API}/reports",
        params={
            "from": FIRST.isoformat(),
            "to": TODAY.isoformat(),
            "category_id": _cat(client, "groceries"),
        },
    ).json()
    assert body["balances_applicable"] is False
    assert body["by_currency"][0]["balances"] is None
    assert body["by_currency"][0]["totals"]["expenses"]["amount"] == "1200.00"


def test_recurring_and_debts_sections(world: tuple[TestClient, str, dict[str, Any]]) -> None:
    client, csrf, accounts = world
    rule = client.post(
        f"{API}/recurring/rules",
        json={
            "kind": "expense",
            "name": "Rent",
            "amount": {"amount": "9000.00", "currency": "TRY"},
            "currency": "TRY",
            "account_id": accounts["bank"]["id"],
            "category_id": _cat(client, "housing"),
            "frequency": "monthly",
            "window_start_day": 1,
            "window_end_day": 31,
            "start_date": FIRST.isoformat(),
        },
        headers=_keyed(csrf),
    )
    assert rule.status_code == 201, rule.text
    body = client.get(
        f"{API}/reports", params={"from": FIRST.isoformat(), "to": TODAY.isoformat()}
    ).json()
    assert [
        (o["name"], o["state"], o["expected"]["amount"], o["actual"]) for o in body["recurring"]
    ] == [("Rent", "pending", "9000.00", None)]
    # Expected money is never in the totals.
    assert body["by_currency"][0]["totals"]["expenses"]["amount"] == "1600.00"
    assert [(d["counterparty"], d["remaining"]["amount"]) for d in body["debts"]] == [
        ("Ahmet", "5000.00")
    ]


# --- Exports


def _csv(client: TestClient, **params: str) -> tuple[bytes, list[dict[str, str]]]:
    response = client.get(
        f"{API}/reports/export.csv",
        params={"from": FIRST.isoformat(), "to": TODAY.isoformat(), **params},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    raw = response.content
    return raw, list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def test_csv_is_utf8_stable_and_signed(world: tuple[TestClient, str, dict[str, Any]]) -> None:
    client, csrf, accounts = world
    _record(
        client, csrf, accounts["cash"], "expense", "45.50", "groceries", description="Şarküteri ığ"
    )
    raw, rows = _csv(client, locale="tr")
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    assert list(rows[0]) == [
        "date",
        "type",
        "description",
        "category",
        "account",
        "counter_account",
        "amount",
        "currency",
        "original_amount",
        "original_currency",
        "fx_rate",
        "fx_method",
        "notes",
        "transaction_id",
        "recorded_at",
    ]
    turkish = next(r for r in rows if r["description"] == "Şarküteri ığ")
    assert (turkish["amount"], turkish["currency"], turkish["category"], turkish["type"]) == (
        "-45.50",
        "TRY",
        "Market",
        "Gider",
    )
    salary = next(r for r in rows if r["description"] == "Salary")
    assert salary["amount"] == "35000.00"


@pytest.mark.parametrize("payload", ['=HYPERLINK("http://x")', "+1+1", "-2+3", "@SUM(A1)"])
def test_csv_neutralizes_formulas(
    world: tuple[TestClient, str, dict[str, Any]], payload: str
) -> None:
    client, csrf, accounts = world
    _record(client, csrf, accounts["cash"], "expense", "1.00", "groceries", description=payload)
    _, rows = _csv(client)
    cell = next(r["description"] for r in rows if r["description"].endswith(payload[1:]))
    assert cell.startswith("'")


def test_csv_includes_original_and_converted_amounts(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, csrf, accounts = world
    response = client.post(
        f"{API}/transactions",
        json={
            "type": "expense",
            "account_id": accounts["bank"]["id"],
            "amount": {"amount": "20.00", "currency": "USD"},
            "category_id": _cat(client, "shopping"),
            "conversion": {"method": "actual_amount", "account_amount": "998.40"},
        },
        headers=_keyed(csrf),
    )
    assert response.status_code == 201, response.text
    _, rows = _csv(client)
    row = next(r for r in rows if r["original_currency"] == "USD")
    assert (row["amount"], row["currency"], row["original_amount"], row["fx_method"]) == (
        "-998.40",
        "TRY",
        "20.00",
        "actual_amount",
    )


def _chrome_available() -> bool:
    return (
        any(
            Path(p).exists()
            for p in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "/usr/bin/google-chrome",
                "/opt/google/chrome/chrome",
            )
        )
        or shutil.which("google-chrome") is not None
    )


@pytest.mark.skipif(not _chrome_available(), reason="PDF export needs a Chrome install")
@pytest.mark.parametrize("locale", ["en", "ar", "tr"])
def test_pdf_has_pages_numbers_and_embedded_fonts(
    world: tuple[TestClient, str, dict[str, Any]], locale: str
) -> None:
    client, csrf, accounts = world
    for i in range(70):  # enough rows to span pages
        _record(
            client,
            csrf,
            accounts["cash"],
            "expense",
            f"{i + 1}.00",
            "groceries",
            description=f"Item {i}",
        )
    response = client.get(
        f"{API}/reports/export.pdf",
        params={"from": FIRST.isoformat(), "to": TODAY.isoformat(), "locale": locale},
    )
    assert response.status_code == 200, response.text
    pdf = response.content
    assert pdf.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert (
        pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page\r") + pdf.count(b"/Type /Page ") >= 2
        or pdf.count(b"/Type/Page") >= 2
    )
    assert b"/FontFile2" in pdf or b"/FontFile3" in pdf  # fonts are embedded


def test_pdf_labels_match_the_apps_category_names() -> None:
    web = Path(__file__).resolve().parents[2] / "apps" / "web" / "messages"
    for locale in ("en", "ar", "tr"):
        names = json.loads((web / f"{locale}.json").read_text(encoding="utf-8"))["app"][
            "categories"
        ]["defaults"]
        for key in CATEGORY_KEYS:
            assert TEXT[locale][f"cat_{key}"] == names[key], (locale, key)
        assert set(TEXT[locale]) == set(TEXT["en"]), locale


def test_hidden_marks_cant_smuggle_a_formula() -> None:
    from app.services.exports import safe_cell

    assert safe_cell("‎=1+1") == "'‎=1+1"
    assert safe_cell("‮	@cmd") == "'‮	@cmd"
    assert safe_cell("Lunch = good") == "Lunch = good"
    assert safe_cell("") == ""


def test_all_history_starts_at_the_first_transaction(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, _, _ = world
    report = client.get(f"{API}/reports", params={"all_history": "true"}).json()
    first = min(
        item["effective_date"]
        for item in client.get(f"{API}/transactions", params={"limit": 100}).json()["items"]
    )
    assert report["filters"]["date_from"] == first
    assert report["filters"]["date_to"] == TODAY.isoformat()
    insights = client.get(f"{API}/insights", params={"all_history": "true"}).json()
    assert insights["date_from"] == first


def test_report_breakdown_can_be_daily_weekly_or_monthly(
    world: tuple[TestClient, str, dict[str, Any]],
) -> None:
    client, _, _ = world
    window = {"from": FIRST.isoformat(), "to": TODAY.isoformat()}
    for grain in ("day", "week", "month"):
        body = client.get(f"{API}/reports", params={**window, "grain": grain}).json()
        section = next(c for c in body["by_currency"] if c["currency"] == "TRY")
        assert section["grain"] == grain
        assert sum(float(p["expenses"]["amount"]) for p in section["series"]) == 1600.0
    assert client.get(f"{API}/reports", params={**window, "grain": "year"}).status_code == 422


def test_previous_period_is_calendar_aware() -> None:
    from app.services.reporting import Scope

    def prev(a: str, b: str) -> tuple[str, str]:
        p = Scope(date.fromisoformat(a), date.fromisoformat(b)).previous()
        return p.date_from.isoformat(), p.date_to.isoformat()

    assert prev("2026-11-01", "2026-11-07") == ("2026-10-01", "2026-10-07")  # month to date
    assert prev("2026-03-01", "2026-03-31") == ("2026-02-01", "2026-02-28")  # whole month
    assert prev("2026-03-01", "2026-03-30") == ("2026-02-01", "2026-02-28")  # cut at month end
    assert prev("2026-09-01", "2026-11-30") == ("2026-06-01", "2026-08-31")  # whole quarter
    assert prev("2026-11-03", "2026-11-09") == ("2026-10-27", "2026-11-02")  # same length


def test_buckets_include_empty_days_weeks_and_months() -> None:
    from app.services.reporting import buckets

    days = buckets(date(2026, 11, 1), date(2026, 11, 7), "day")
    assert len(days) == 7 and days[0] == date(2026, 11, 1)
    weeks = buckets(date(2026, 11, 1), date(2026, 11, 30), "week")
    assert weeks[0] == date(2026, 10, 26) and all(w.weekday() == 0 for w in weeks)
    months = buckets(date(2025, 12, 15), date(2026, 11, 7), "month")
    assert len(months) == 12 and months[0] == date(2025, 12, 1) and months[-1] == date(2026, 11, 1)


def test_csv_signs_debts_like_the_app(world: tuple[TestClient, str, dict[str, Any]]) -> None:
    client, csrf, accounts = world
    lent = client.post(
        f"{API}/debts",
        json={
            "direction": "owed_to_me",
            "counterparty": "Zeynep",
            "amount": {"amount": "750.00", "currency": "TRY"},
            "account_id": accounts["cash"]["id"],
        },
        headers=_keyed(csrf),
    )
    assert lent.status_code == 201, lent.text
    body = client.get(f"{API}/reports/export.csv").content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(body)))
    debts = {r["counter_account"]: r["amount"] for r in rows if r["type"] == "Debt"}
    assert debts == {"Ahmet": "5000.00", "Zeynep": "-750.00"}  # borrowed in, lent out


def test_insights_count_entries_like_home(world: tuple[TestClient, str, dict[str, Any]]) -> None:
    client, _, _ = world
    home = client.get(f"{API}/summary", params={"month": MONTH}).json()["currencies"][0]
    window = {"from": FIRST.isoformat(), "to": TODAY.isoformat()}
    insights = client.get(f"{API}/insights", params=window).json()
    report = client.get(f"{API}/reports", params=window).json()
    assert insights["totals"]["transactions"] == home["transactions"] == 4  # income + expenses
    assert report["by_currency"][0]["totals"]["transactions"] == 7  # every posted transaction
