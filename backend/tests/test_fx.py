"""M5.1 to M5.3: exchange rates, conversion when recording, and Amount Equivalent.

Frankfurter and Coinbase are replaced at the HTTP layer by a fake (MOCKED, tests only): the real
adapters, parsing, caches, and checks run unchanged. No test reaches the network.
"""

import json
import uuid
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ApiError
from app.db.models import FxRate, JournalLine, Transaction
from app.services import equivalents, fx
from app.services.fx_providers import (
    Coinbase,
    Frankfurter,
    PairUnsupportedError,
    ProviderUnavailableError,
)
from tests.auth_harness import sign_in, write_headers
from tests.google_fakes import FakeGoogle

API = "/api/v1"
# The test users live in UTC (the default time zone), so "today" is the UTC date.
TODAY = datetime.now(UTC).date()


class FakeProviders:
    """Serves Frankfurter v2 and Coinbase answers from a table, and records every request."""

    def __init__(self) -> None:
        self.rates: dict[tuple[str, str], str] = {
            ("USD", "EUR"): "0.89075",
            ("USD", "TRY"): "50.153",
            ("EUR", "TRY"): "56.31",
            ("TRY", "USD"): "0.019939",
            ("TRY", "EUR"): "0.017759",
            ("TRY", "GBP"): "0.015212",
            ("XAU", "TRY"): "205039",
            ("USD", "JPY"): "149.555",
            ("USD", "KWD"): "0.30745",
            ("GBP", "USD"): "1.0025",
        }
        self.ecb: set[tuple[str, str]] = {("USD", "EUR"), ("USD", "TRY"), ("EUR", "TRY")}
        self.btc: dict[str, str] = {"TRY": "3797806.622405568371654823", "USD": "75729.37"}
        self.shift: dict[str, str] = {}  # requested date -> date the "source" returns
        self.mode = "ok"  # "ok", "down", "garbage", "flaky"
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.mode == "down":
            raise httpx.ConnectTimeout("simulated", request=request)
        if self.mode == "flaky":
            self.mode = "ok"
            return httpx.Response(503, content=b"busy")
        if self.mode == "garbage":
            return httpx.Response(200, content=b'{"date":"x","base":"USD","quote":"EUR","rate":-1}')
        path = request.url.path.split("/")
        if request.url.host == "api.coinbase.com":
            pair = path[3].split("-")
            if pair[1] not in self.btc:
                return httpx.Response(404, content=b'{"errors":[]}')
            body = {"data": {"amount": self.btc[pair[1]], "base": "BTC", "currency": pair[1]}}
            return httpx.Response(200, content=json.dumps(body).encode())
        base, quote = path[3].upper(), path[4].upper()
        params = dict(request.url.params)
        if params.get("providers") == "ECB" and (base, quote) not in self.ecb:
            return httpx.Response(404, content=b'{"status":404,"message":"not found"}')
        if (base, quote) not in self.rates:
            return httpx.Response(422, content=b'{"status":422,"message":"invalid currency"}')
        asked = params.get("date", TODAY.isoformat())
        returned = self.shift.get(asked, asked)
        # A JSON number, as the real API sends: it must be read as Decimal, never float.
        rate = self.rates[(base, quote)]
        raw = f'{{"date":"{returned}","base":"{base}","quote":"{quote}","rate":{rate}}}'
        return httpx.Response(200, content=raw.encode())

    def gateway(self, stale_days: int = 4) -> fx.FxGateway:
        transport = httpx.MockTransport(self.handler)
        return fx.FxGateway(
            frankfurter=Frankfurter(1, transport),
            coinbase=Coinbase(1, transport),
            stale_days=stale_days,
        )


@pytest.fixture
def providers(auth_app: FastAPI) -> Iterator[FakeProviders]:
    fake = FakeProviders()
    auth_app.state.fx = fake.gateway()
    yield fake


def _keyed(csrf: str, key: str | None = None) -> dict[str, str]:
    return {**write_headers(csrf), "Idempotency-Key": key or str(uuid.uuid4())}


def _setup(client: TestClient, csrf: str, currency: str) -> dict[str, Any]:
    assert (
        client.post(
            f"{API}/setup", json={"currency": currency}, headers=write_headers(csrf)
        ).status_code
        == 200
    )
    return next(a for a in client.get(f"{API}/accounts").json() if a["is_default"])


def _category(client: TestClient, key: str = "food_and_drinks") -> str:
    return str(
        next(c["id"] for c in client.get(f"{API}/categories").json() if c["system_key"] == key)
    )


def _expense(
    client: TestClient,
    csrf: str,
    account: dict[str, Any],
    amount: str,
    currency: str,
    conversion: dict[str, Any] | None,
    key: str | None = None,
    **extra: Any,
) -> Any:
    body: dict[str, Any] = {
        "type": "expense",
        "account_id": account["id"],
        "amount": {"amount": amount, "currency": currency},
        "category_id": _category(client),
        **extra,
    }
    if conversion is not None:
        body["conversion"] = conversion
    return client.post(f"{API}/transactions", json=body, headers=_keyed(csrf, key))


def _preview(
    client: TestClient, base: str, quote: str, amount: str | None = None, on: date | None = None
) -> Any:
    params = {"from": base, "to": quote}
    if amount:
        params["amount"] = amount
    if on:
        params["date"] = on.isoformat()
    return client.get(f"{API}/fx/convert", params=params)


# --- Providers and arithmetic (no database)


def test_frankfurter_reads_rates_as_exact_decimals() -> None:
    fake = FakeProviders()
    quote = Frankfurter(1, httpx.MockTransport(fake.handler)).daily("USD", "EUR", None, TODAY)
    assert quote.rate == Decimal("0.89075")
    assert isinstance(quote.rate, Decimal)
    assert (quote.provider, quote.upstream, quote.rate_date) == ("frankfurter", "ECB", TODAY)


def test_frankfurter_falls_back_to_blended_when_ecb_has_no_pair() -> None:
    fake = FakeProviders()
    quote = Frankfurter(1, httpx.MockTransport(fake.handler)).daily("XAU", "TRY", None, TODAY)
    assert (quote.upstream, quote.rate) == ("blended", Decimal("205039"))
    assert len(fake.calls) == 2


def test_unknown_pairs_are_unsupported_not_unavailable() -> None:
    fake = FakeProviders()
    with pytest.raises(PairUnsupportedError):
        Frankfurter(1, httpx.MockTransport(fake.handler)).daily("USD", "ZAR", None, TODAY)


@pytest.mark.parametrize(
    "body",
    [
        b'{"date":"2026-11-02","base":"USD","quote":"EUR","rate":-1}',
        b'{"date":"2026-11-02","base":"USD","quote":"EUR","rate":0}',
        b'{"date":"2026-11-02","base":"USD","quote":"EUR","rate":"abc"}',
        b'{"date":"2026-11-02","base":"USD","quote":"EUR","rate":1e40}',
        b'{"date":"2026-11-02","base":"USD","quote":"GBP","rate":0.8}',
        b'{"date":"not a date","base":"USD","quote":"EUR","rate":0.8}',
        b'{"date":"2099-01-01","base":"USD","quote":"EUR","rate":0.8}',
        b"[1, 2, 3]",
        b"<html>maintenance</html>",
    ],
)
def test_nonsensical_answers_are_rejected(body: bytes) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    with pytest.raises(ProviderUnavailableError):
        Frankfurter(1, transport).daily("USD", "EUR", date(2026, 11, 2), date(2026, 11, 3))


def test_a_rate_dated_after_the_requested_day_is_refused() -> None:
    fake = FakeProviders()
    fake.shift["2026-11-02"] = "2026-11-03"
    with pytest.raises(ProviderUnavailableError):
        Frankfurter(1, httpx.MockTransport(fake.handler)).daily(
            "USD", "EUR", date(2026, 11, 2), date(2026, 11, 5)
        )


def test_timeouts_are_retried_once_then_reported() -> None:
    fake = FakeProviders()
    fake.mode = "down"
    with pytest.raises(ProviderUnavailableError):
        Frankfurter(1, httpx.MockTransport(fake.handler)).daily("USD", "EUR", None, TODAY)
    assert len(fake.calls) == 2


def test_a_server_error_is_retried_once() -> None:
    fake = FakeProviders()
    fake.mode = "flaky"
    quote = Frankfurter(1, httpx.MockTransport(fake.handler)).daily("USD", "EUR", None, TODAY)
    assert quote.rate == Decimal("0.89075")
    assert len(fake.calls) == 2


def test_coinbase_prices_only_bitcoin() -> None:
    fake = FakeProviders()
    client = Coinbase(1, httpx.MockTransport(fake.handler))
    assert client.spot("BTC", "TRY").rate == Decimal("3797806.622405568371654823")
    with pytest.raises(PairUnsupportedError):
        client.spot("ETH", "USD")
    with pytest.raises(PairUnsupportedError):
        client.spot("BTC", "ZAR")


@pytest.mark.parametrize(
    ("minor", "from_exp", "to_exp", "rate", "expected"),
    [
        (2200, 2, 2, "0.89075", 1960),  # 19.5965 EUR -> 19.60
        (200, 2, 2, "1.0025", 201),  # exactly half: up (a float would give 200)
        (1000, 2, 0, "149.555", 1496),  # 1495.55 JPY -> 1496
        (1000, 2, 3, "0.30745", 3075),  # 3.0745 KWD -> 3.075
        (1, 2, 2, "0.5", 1),  # 0.005 -> 0.01
        (12345, 0, 2, "0.0331", 40862),  # JPY to USD
    ],
)
def test_conversion_rounds_once_half_up(
    minor: int, from_exp: int, to_exp: int, rate: str, expected: int
) -> None:
    assert fx.convert_minor(minor, from_exp, to_exp, Decimal(rate)) == expected


def test_a_conversion_that_rounds_to_zero_is_refused() -> None:
    with pytest.raises(ApiError) as caught:
        fx.convert_minor(1, 2, 2, Decimal("0.001"))
    assert caught.value.code == "conversion_rounds_to_zero"


def test_implied_rate_and_manual_rate_parsing() -> None:
    assert fx.implied_rate(2200, 2, 1985, 2) == Decimal("0.902272727273")
    assert fx.parse_manual_rate("0.9") == Decimal("0.9")
    for bad in ("-1", "0", "1e3", "1,5", "abc", "0.0000000000001", "9999999999999"):
        with pytest.raises(ApiError):
            fx.parse_manual_rate(bad)


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        ("0.013182345", 8, "0.01318"),
        ("243.8561", 3, "243.9"),
        ("12345.678", 3, "12346"),  # whole units: never fewer than 0 decimals
        ("0.000000123456", 8, "0.00000012"),
        ("7.98765", 4, "7.988"),
    ],
)
def test_equivalents_avoid_false_precision(value: str, places: int, expected: str) -> None:
    assert format(equivalents.significant(Decimal(value), places), "f") == expected


# --- Rates through the API


def test_a_preview_stores_one_observation_and_is_cached(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    sign_in(browser, google)
    first = _preview(browser, "USD", "EUR", "22.00")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["original"] == {"amount": "22.00", "currency": "USD"}
    assert body["converted"] == {"amount": "19.60", "currency": "EUR"}
    assert body["rate"]["rate"] == "0.89075"
    assert (body["rate"]["provider"], body["rate"]["upstream"]) == ("frankfurter", "ECB")
    assert not body["rate"]["is_stale"] and not body["rate"]["fallback"]
    calls = len(providers.calls)
    again = _preview(browser, "USD", "EUR", "50.00")
    assert again.json()["rate"]["id"] == body["rate"]["id"]
    assert len(providers.calls) == calls  # no second request
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FxRate)) == 1


def test_historical_rates_keep_the_date_the_source_returned(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    sign_in(browser, google)
    providers.shift["2026-11-01"] = "2026-10-30"  # a Sunday answers with Friday's rate
    body = _preview(browser, "USD", "EUR", on=date(2026, 11, 1)).json()["rate"]
    assert (body["requested_date"], body["rate_date"], body["age_days"]) == (
        "2026-11-01",
        "2026-10-30",
        2,
    )
    assert body["is_stale"] is False


def test_future_dates_unsupported_pairs_and_same_currency_are_refused(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    sign_in(browser, google)
    assert (
        _preview(browser, "USD", "EUR", on=TODAY + timedelta(days=3)).json()["code"]
        == "date_in_future"
    )
    assert _preview(browser, "USD", "ZAR").json()["code"] == "fx_pair_unsupported"
    assert _preview(browser, "USD", "USD").json()["code"] == "same_currency"
    assert _preview(browser, "USD", "QQQ").json()["code"] == "unsupported_currency"


def test_provider_failure_never_invents_a_rate(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    sign_in(browser, google)
    providers.mode = "down"
    response = _preview(browser, "USD", "EUR", "22.00")
    assert (response.status_code, response.json()["code"]) == (503, "fx_unavailable")


def test_a_stored_rate_is_offered_as_a_labelled_fallback_when_the_provider_is_down(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    sign_in(browser, google)
    old = TODAY - timedelta(days=10)
    assert _preview(browser, "USD", "EUR", on=old).status_code == 200
    providers.mode = "down"
    auth_app.state.fx.reset()
    body = _preview(browser, "USD", "EUR").json()["rate"]
    assert body["fallback"] is True
    assert body["rate_date"] == old.isoformat()
    assert body["is_stale"] is True and body["age_days"] == 10


def test_rates_are_append_only(
    factory: sessionmaker[Session],
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
) -> None:
    sign_in(browser, google)
    _preview(browser, "USD", "EUR")
    with factory() as db, pytest.raises(DBAPIError):
        db.execute(text("UPDATE fx_rates SET rate = 1"))
        db.commit()
    with factory() as db, pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM fx_rates"))
        db.commit()


# --- Recording in another currency


def _lines_by_currency(factory: sessionmaker[Session], transaction_id: str) -> dict[str, int]:
    with factory() as db:
        from app.db.models import JournalEntry

        totals: dict[str, int] = defaultdict(int)
        for currency, amount in db.execute(
            select(JournalLine.currency, JournalLine.amount_minor)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalEntry.transaction_id == uuid.UUID(transaction_id))
        ):
            totals[currency] += abs(amount)
    return dict(totals)


def test_a_dollar_expense_from_a_euro_account_posts_euros_once(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    rate_id = _preview(browser, "USD", "EUR", "22.00").json()["rate"]["id"]
    response = _expense(
        browser, csrf, cash, "22.00", "USD", {"method": "reference", "rate_id": rate_id}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == {"amount": "19.60", "currency": "EUR"}
    assert body["original_amount"] == {"amount": "22.00", "currency": "USD"}
    assert body["fx"]["method"] == "reference" and body["fx"]["rate"] == "0.89075"
    assert body["fx"]["upstream"] == "ECB"
    # The ledger holds euros only: the dollar amount is a memo, never a second posting.
    assert _lines_by_currency(factory, body["id"]) == {"EUR": 3920}
    assert browser.get(f"{API}/accounts/{cash['id']}").json()["balance"]["amount"] == "-19.60"
    month = TODAY.strftime("%Y-%m")
    summary = browser.get(f"{API}/summary", params={"month": month}).json()["currencies"]
    assert [(c["currency"], c["expenses"]["amount"]) for c in summary] == [("EUR", "19.60")]


def test_the_amount_actually_charged_is_recorded_exactly(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    rate_id = _preview(browser, "USD", "EUR", "22.00").json()["rate"]["id"]
    body = _expense(
        browser, csrf, cash, "22.00", "USD",
        {"method": "actual_amount", "account_amount": "19.85", "rate_id": rate_id},
    ).json()  # fmt: skip
    assert body["amount"]["amount"] == "19.85"
    assert body["fx"] == {
        "method": "actual_amount", "rate": "0.902272727273", "rate_date": TODAY.isoformat(),
        "source": None, "provider": None, "upstream": None,
    }  # fmt: skip
    assert body["warnings"] == []


def test_a_typed_rate_is_stored_and_a_far_one_is_flagged_not_blocked(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    _preview(browser, "USD", "EUR")  # a reference is stored, so the guard can compare
    close = _expense(
        browser, csrf, cash, "10.00", "USD", {"method": "manual_rate", "rate": "0.9"}
    ).json()
    assert (close["amount"]["amount"], close["warnings"]) == ("9.00", [])
    far = _expense(
        browser, csrf, cash, "10.00", "USD", {"method": "manual_rate", "rate": "1.2"}
    ).json()
    assert (far["amount"]["amount"], far["warnings"]) == ("12.00", ["rate_far_from_reference"])
    with factory() as db:
        manual = db.scalars(select(FxRate).where(FxRate.source == "manual")).all()
    assert sorted(str(r.rate.normalize()) for r in manual) == ["0.9", "1.2"]


def test_a_stale_reference_needs_acknowledgement(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    old = _preview(browser, "USD", "EUR", on=TODAY - timedelta(days=9)).json()["rate"]["id"]
    refused = _expense(browser, csrf, cash, "5.00", "USD", {"method": "reference", "rate_id": old})
    assert (refused.status_code, refused.json()["code"]) == (409, "fx_rate_stale")
    accepted = _expense(
        browser,
        csrf,
        cash,
        "5.00",
        "USD",
        {"method": "reference", "rate_id": old, "accept_stale": True},
    )
    assert accepted.status_code == 201


@pytest.mark.parametrize(
    ("currency", "conversion", "code"),
    [
        ("USD", None, "conversion_required"),
        ("EUR", {"method": "manual_rate", "rate": "1"}, "conversion_not_needed"),
        ("USD", {"method": "reference"}, "validation_error"),
        ("USD", {"method": "reference", "rate_id": str(uuid.uuid4())}, "fx_rate_mismatch"),
        ("USD", {"method": "manual_rate", "rate": "-2"}, "invalid_rate"),
        ("USD", {"method": "actual_amount", "account_amount": "1.234"}, "invalid_amount"),
        ("USD", {"method": "actual_amount", "account_amount": "0"}, "amount_not_positive"),
        ("USD", {"method": "guess"}, "validation_error"),
    ],
)
def test_bad_conversions_record_nothing(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
    currency: str,
    conversion: dict[str, Any] | None,
    code: str,
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    response = _expense(browser, csrf, cash, "5.00", currency, conversion)
    assert response.json()["code"] == code, response.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0


def test_a_rate_for_another_pair_or_a_manual_rate_cant_be_passed_off_as_a_reference(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    wrong_pair = _preview(browser, "USD", "TRY").json()["rate"]["id"]
    response = _expense(
        browser, csrf, cash, "5.00", "USD", {"method": "reference", "rate_id": wrong_pair}
    )
    assert response.json()["code"] == "fx_rate_mismatch"
    _expense(browser, csrf, cash, "5.00", "USD", {"method": "manual_rate", "rate": "0.9"})
    with factory() as db:
        manual = db.scalar(select(FxRate.id).where(FxRate.source == "manual"))
    as_reference = _expense(
        browser, csrf, cash, "5.00", "USD", {"method": "reference", "rate_id": str(manual)}
    )
    assert as_reference.json()["code"] == "fx_rate_mismatch"


def test_a_retried_save_records_once_even_with_a_typed_rate(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    key = str(uuid.uuid4())
    first = _expense(
        browser, csrf, cash, "10.00", "USD", {"method": "manual_rate", "rate": "0.9"}, key=key
    )
    second = _expense(
        browser, csrf, cash, "10.00", "USD", {"method": "manual_rate", "rate": "0.9"}, key=key
    )
    assert first.json() == second.json()
    assert second.headers.get("Idempotent-Replayed") == "true"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
        assert (
            db.scalar(select(func.count()).select_from(FxRate).where(FxRate.source == "manual"))
            == 1
        )


def test_corrections_keep_the_stored_rate_and_reverse_cleanly(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    rate_id = _preview(browser, "USD", "EUR").json()["rate"]["id"]
    created = _expense(
        browser, csrf, cash, "22.00", "USD", {"method": "reference", "rate_id": rate_id}
    ).json()
    # The market moves; the record doesn't.
    providers.rates[("USD", "EUR")] = "0.95"
    corrected = browser.patch(
        f"{API}/transactions/{created['id']}",
        json={"amount": {"amount": "30.00", "currency": "USD"}},
        headers={**write_headers(csrf), "If-Match": f'"{created["version"]}"'},
    )
    assert corrected.status_code == 200, corrected.text
    body = corrected.json()
    assert body["amount"]["amount"] == "26.72"  # 30 * 0.89075 = 26.7225
    assert body["fx"]["rate"] == "0.89075"
    # Moving it to yesterday keeps the same rate too.
    moved = browser.patch(
        f"{API}/transactions/{created['id']}",
        json={"effective_date": (TODAY - timedelta(days=1)).isoformat()},
        headers={**write_headers(csrf), "If-Match": f'"{body["version"]}"'},
    ).json()
    assert moved["amount"]["amount"] == "26.72"
    assert browser.get(f"{API}/accounts/{cash['id']}").json()["balance"]["amount"] == "-26.72"
    with factory() as db:
        sums: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        for entry_id, currency, amount in db.execute(
            select(JournalLine.entry_id, JournalLine.currency, JournalLine.amount_minor)
        ):
            sums[(entry_id, currency)] += amount
    assert all(v == 0 for v in sums.values())


def test_changing_an_amount_typed_by_hand_needs_the_new_charged_amount(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    created = _expense(
        browser, csrf, cash, "22.00", "USD", {"method": "actual_amount", "account_amount": "19.85"}
    ).json()
    headers = {**write_headers(csrf), "If-Match": f'"{created["version"]}"'}
    refused = browser.patch(
        f"{API}/transactions/{created['id']}",
        json={"amount": {"amount": "25.00", "currency": "USD"}},
        headers=headers,
    )
    assert refused.json()["code"] == "conversion_required"
    fixed = browser.patch(
        f"{API}/transactions/{created['id']}",
        json={
            "amount": {"amount": "25.00", "currency": "USD"},
            "conversion": {"method": "actual_amount", "account_amount": "22.40"},
        },
        headers=headers,
    )
    assert fixed.json()["amount"]["amount"] == "22.40"
    # A category change alone keeps everything.
    recat = browser.patch(
        f"{API}/transactions/{created['id']}",
        json={"category_id": _category(browser, "groceries")},
        headers={**write_headers(csrf), "If-Match": f'"{fixed.json()["version"]}"'},
    ).json()
    assert (recat["amount"]["amount"], recat["original_amount"]["amount"]) == ("22.40", "25.00")


def test_another_users_cannot_use_my_rates_or_see_my_conversions(
    auth_app: FastAPI,
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    csrf = sign_in(browser, google)
    cash = _setup(browser, csrf, "EUR")
    created = _expense(
        browser, csrf, cash, "10.00", "USD", {"method": "manual_rate", "rate": "0.9"}
    ).json()
    with factory() as db:
        manual = db.scalar(select(FxRate).where(FxRate.source == "manual"))
        assert manual is not None
    with TestClient(auth_app) as other:
        other_csrf = sign_in(other, google, subject="g-other", email="other@example.com")
        other_cash = _setup(other, other_csrf, "EUR")
        stolen = _expense(
            other,
            other_csrf,
            other_cash,
            "10.00",
            "USD",
            {"method": "reference", "rate_id": str(manual.id)},
        )
        assert stolen.json()["code"] == "fx_rate_mismatch"
        assert other.get(f"{API}/transactions/{created['id']}").status_code == 404


# --- Amount Equivalent


def test_equivalents_show_currencies_gold_and_bitcoin_without_touching_money(
    browser: TestClient,
    google: FakeGoogle,
    providers: FakeProviders,
    factory: sessionmaker[Session],
) -> None:
    csrf = sign_in(browser, google)
    _setup(browser, csrf, "TRY")
    with factory() as db:
        before = (
            db.scalar(select(func.count()).select_from(Transaction)),
            db.scalar(select(func.count()).select_from(JournalLine)),
        )
    response = browser.get(
        f"{API}/equivalents",
        params={"amount": "50000.00", "currency": "TRY", "targets": "USD,XAU_G,XAU_OZ,BTC,TRY"},
    )
    assert response.status_code == 200, response.text
    items = {i["target"]: i for i in response.json()["items"]}
    assert set(items) == {"USD", "XAU_G", "XAU_OZ", "BTC"}  # the same currency is skipped
    # USD: 50000 / (1 / ...) uses the USD->TRY price: 50000 / 50.153
    assert items["USD"]["value"] == "996.95" and items["USD"]["kind"] == "daily_reference"
    # Gold: 205039 TRY per troy ounce; a troy ounce is 31.1034768 g.
    assert items["XAU_OZ"]["value"] == "0.2439"
    assert items["XAU_G"]["value"] == "7.585"
    assert items["XAU_G"]["unit_price"] == "6592.16"
    assert items["BTC"]["value"] == "0.01317" and items["BTC"]["kind"] == "spot"
    assert items["BTC"]["provider"] == "coinbase"
    with factory() as db:
        after = (
            db.scalar(select(func.count()).select_from(Transaction)),
            db.scalar(select(func.count()).select_from(JournalLine)),
        )
    assert before == after


def test_an_unavailable_quote_says_so_and_shows_no_number(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    sign_in(browser, google)
    providers.mode = "down"
    items = browser.get(
        f"{API}/equivalents", params={"amount": "100.00", "currency": "TRY", "targets": "USD,BTC"}
    ).json()["items"]
    assert [(i["target"], i["status"], i["value"]) for i in items] == [
        ("USD", "unavailable", None),
        ("BTC", "unavailable", None),
    ]


def test_favourites_default_and_can_be_changed_with_csrf(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    csrf = sign_in(browser, google)
    assert browser.get(f"{API}/equivalents/targets").json() == {
        "targets": ["USD", "EUR", "XAU_G", "BTC"]
    }
    assert browser.put(f"{API}/equivalents/targets", json={"targets": ["GBP"]}).status_code == 403
    saved = browser.put(
        f"{API}/equivalents/targets",
        json={"targets": ["gbp", "XAU_OZ", "GBP"]},
        headers=write_headers(csrf),
    )
    assert saved.json() == {"targets": ["GBP", "XAU_OZ"]}
    bad = browser.put(
        f"{API}/equivalents/targets", json={"targets": ["DOGE"]}, headers=write_headers(csrf)
    )
    assert bad.status_code == 422
    items = browser.get(f"{API}/equivalents", params={"amount": "100.00", "currency": "TRY"}).json()
    assert [i["target"] for i in items["items"]] == ["GBP", "XAU_OZ"]


def test_a_typed_rate_can_be_previewed_without_any_provider_call(
    browser: TestClient, google: FakeGoogle, providers: FakeProviders
) -> None:
    sign_in(browser, google)
    response = browser.get(
        f"{API}/fx/convert", params={"from": "USD", "to": "EUR", "amount": "22.00", "rate": "0.9"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "rate": None,
        "original": {"amount": "22.00", "currency": "USD"},
        "converted": {"amount": "19.80", "currency": "EUR"},
    }
    assert providers.calls == []
    bad = browser.get(f"{API}/fx/convert", params={"from": "USD", "to": "EUR", "rate": "abc"})
    assert bad.json()["code"] == "invalid_rate"
