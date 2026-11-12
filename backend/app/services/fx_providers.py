"""Exchange-rate and price providers (FINANCIAL_RULES.md §7). Server side only; no keys.

Two adapters, behind one small interface so either can be replaced without touching the ledger:

- **Frankfurter v2** (`api.frankfurter.dev`): daily reference rates published by central banks, for
  currencies and gold (`XAU`, priced per troy ounce of fine gold). A rate is a reference for its
  date, never a real-time or executable price. ECB is asked first, as one named source; pairs it
  doesn't publish fall back to Frankfurter's blended rate, recorded as `upstream = "blended"`.
- **Coinbase** (`api.coinbase.com/v2/prices/BTC-XXX/spot`): an indicative Bitcoin spot price, used
  only to *display* equivalents. It is never stored and never used to post money.

Every response is parsed with `Decimal` (never float), checked for shape, sign, size and date, and
any failure becomes `ProviderUnavailableError` with a short reason for logs. Hosts are fixed here:
no URL comes from configuration or from users (SECURITY.md, SSRF).
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx2 as httpx

FRANKFURTER = "https://api.frankfurter.dev"
COINBASE = "https://api.coinbase.com"
_CODE = re.compile(r"^[A-Z]{3}$")

# Anything outside these bounds is a broken response, not a market price.
MIN_RATE = Decimal("1e-9")
MAX_RATE = Decimal("1e12")


class ProviderUnavailableError(Exception):
    """No usable answer: network, timeout, status, or a response that failed the checks."""


class PairUnsupportedError(Exception):
    """The provider says it has no rate for this pair (cached longer than a failure)."""


@dataclass(frozen=True)
class Quote:
    base: str
    quote: str
    rate: Decimal  # 1 base = rate quote
    rate_date: date | None  # the date the source says the rate is for (daily references)
    observed_at: datetime  # when FinPilot received it
    provider: str
    upstream: str
    kind: str  # "daily" (reference for a date) or "spot" (indicative, display only)


def check_rate(value: object) -> Decimal:
    """A positive, finite, sensible decimal, or ProviderUnavailableError."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ProviderUnavailableError("rate has the wrong type")
    try:
        rate = Decimal(value)
    except InvalidOperation:
        raise ProviderUnavailableError("rate isn't a number") from None
    if not rate.is_finite() or rate < MIN_RATE or rate > MAX_RATE:
        raise ProviderUnavailableError("rate out of bounds")
    return rate


class _Http:
    """GET with a strict timeout, at most one retry for a timeout or 5xx, and Decimal JSON."""

    def __init__(self, timeout_seconds: float, transport: httpx.BaseTransport | None) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    def get_json(self, url: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
        last: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=self._timeout, transport=self._transport, follow_redirects=False
                ) as client:
                    response = client.get(
                        url, params=params, headers={"Accept": "application/json"}
                    )
            except httpx.HTTPError as error:
                last = error
            else:
                if response.status_code >= 500:
                    last = ProviderUnavailableError(f"status {response.status_code}")
                else:
                    if len(response.content) > 64_000:
                        raise ProviderUnavailableError("response too large")
                    try:
                        data = json.loads(response.content, parse_float=Decimal)
                    except ValueError:
                        raise ProviderUnavailableError("invalid JSON") from None
                    return response.status_code, data
            if attempt == 0:
                time.sleep(0.2)
        raise ProviderUnavailableError(type(last).__name__ if last else "request failed")


class Frankfurter:
    name = "frankfurter"

    def __init__(
        self, timeout_seconds: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = _Http(timeout_seconds, transport)

    def daily(self, base: str, quote: str, on: date | None, today: date) -> Quote:
        """The reference rate for `on` (or the latest), ECB first, then blended."""
        if not (_CODE.match(base) and _CODE.match(quote)) or base == quote:
            raise PairUnsupportedError("invalid pair")
        params = {"date": on.isoformat()} if on else {}
        url = f"{FRANKFURTER}/v2/rate/{base.lower()}/{quote.lower()}"
        for upstream in ("ECB", "blended"):
            query = {**params, "providers": "ECB"} if upstream == "ECB" else params
            status, data = self._http.get_json(url, query)
            if status in (404, 422) or (status == 400 and upstream == "ECB"):
                continue  # not published by this source: try the next
            if status != 200:
                raise ProviderUnavailableError(f"status {status}")
            return self._parse(data, base, quote, on, today, upstream)
        raise PairUnsupportedError("no source publishes this pair")

    def _parse(
        self, data: object, base: str, quote: str, on: date | None, today: date, upstream: str
    ) -> Quote:
        if not isinstance(data, dict):
            raise ProviderUnavailableError("unexpected shape")
        if str(data.get("base", "")).upper() != base or str(data.get("quote", "")).upper() != quote:
            raise ProviderUnavailableError("answered a different pair")
        try:
            rate_date = date.fromisoformat(str(data.get("date")))
        except ValueError:
            raise ProviderUnavailableError("invalid date") from None
        # Asia-Pacific sources publish "tomorrow" first, so one day of tolerance; never more.
        if rate_date > today + timedelta(days=1) or (on is not None and rate_date > on):
            raise ProviderUnavailableError("rate dated after the requested day")
        if rate_date < date(1999, 1, 1):
            raise ProviderUnavailableError("implausible date")
        return Quote(
            base=base,
            quote=quote,
            rate=check_rate(data.get("rate")),
            rate_date=rate_date,
            observed_at=datetime.now(UTC),
            provider=self.name,
            upstream=upstream,
            kind="daily",
        )


class Coinbase:
    name = "coinbase"

    def __init__(
        self, timeout_seconds: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = _Http(timeout_seconds, transport)

    def spot(self, base: str, quote: str) -> Quote:
        if base != "BTC" or not _CODE.match(quote):
            raise PairUnsupportedError("only BTC is priced here")
        status, data = self._http.get_json(f"{COINBASE}/v2/prices/{base}-{quote}/spot")
        if status in (400, 404, 422):
            raise PairUnsupportedError("pair not quoted")
        if status != 200:
            raise ProviderUnavailableError(f"status {status}")
        body = data.get("data") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            raise ProviderUnavailableError("unexpected shape")
        if (
            str(body.get("base", "")).upper() != base
            or str(body.get("currency", "")).upper() != quote
        ):
            raise ProviderUnavailableError("answered a different pair")
        return Quote(
            base=base,
            quote=quote,
            rate=check_rate(body.get("amount")),
            rate_date=None,
            observed_at=datetime.now(UTC),
            provider=self.name,
            upstream="spot",
            kind="spot",
        )
