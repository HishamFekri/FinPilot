"""Exchange rates, conversion, and Amount Equivalent (FINANCIAL_RULES.md §4.6, §7, §7a).

Two paths, kept apart on purpose:

- **Recording money** uses daily reference rates stored in `fx_rates` (or a rate the person types).
  A preview fetches and stores the reference; saving only *references* that stored row, so posting
  never waits on the network and a recorded conversion can always be reproduced. Conversion rounds
  once, half up, to the account currency's minor unit (§8.2).
- **Amount Equivalent** is display only. It reads the same daily references for currencies and
  gold, and an indicative Bitcoin spot price that is cached in memory and never stored.

Outbound calls are bounded: an in-memory cache per question, a short negative cache after a failure,
a longer one for unsupported pairs, and a global per-minute budget. A rate is never invented: when
nothing usable exists, the answer is 503 `fx_unavailable` and the person can enter their own.
"""

import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import MAX_MINOR, FxRate, User
from app.services.fx_providers import (
    Coinbase,
    Frankfurter,
    PairUnsupportedError,
    ProviderUnavailableError,
    Quote,
    check_rate,
)

RATE_PLACES = Decimal("0.000000000001")  # NUMERIC(28,12), M4
TROY_OUNCE_GRAMS = Decimal("31.1034768")  # exact, by definition of the troy ounce
MANUAL_DEVIATION = Decimal("0.10")  # §7.3: warn above 10%, never block
_MANUAL_RATE = re.compile(r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,12})?$")


class _TtlCache:
    """A small thread-safe LRU with per-entry expiry. Bounded, so it can't grow without limit."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            found = self._items.get(key)
            if found is None:
                return None
            if found[0] < time.monotonic():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return found[1]

    def put(self, key: Any, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._items[key] = (time.monotonic() + ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class _Budget:
    """At most `per_minute` outbound calls across the whole process."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if t > now - 60]
            if len(self._calls) >= self._per_minute:
                return False
            self._calls.append(now)
            return True


@dataclass(frozen=True)
class RateView:
    """A stored daily reference (or manual rate), with how fit it is for the day asked about."""

    id: uuid.UUID
    base: str
    quote: str
    rate: Decimal
    rate_date: date
    requested_date: date
    source: str
    provider: str | None
    upstream: str | None
    retrieved_at: datetime
    age_days: int
    is_stale: bool
    fallback: bool  # the provider couldn't be reached; this is an older stored rate


def _view(row: FxRate, requested: date, stale_days: int, fallback: bool) -> RateView:
    age = (requested - row.rate_date).days
    return RateView(
        id=row.id,
        base=row.base,
        quote=row.quote,
        rate=row.rate,
        rate_date=row.rate_date,
        requested_date=requested,
        source=row.source,
        provider=row.provider,
        upstream=row.upstream,
        retrieved_at=row.retrieved_at,
        age_days=max(age, 0),
        is_stale=age > stale_days,
        fallback=fallback,
    )


def unavailable() -> ApiError:
    return ApiError(503, "fx_unavailable", "No exchange rate is available right now")


class FxGateway:
    """Per-process access to the providers, with the caches and the outbound budget."""

    def __init__(
        self,
        *,
        frankfurter: Frankfurter,
        coinbase: Coinbase,
        stale_days: int = 4,
        outbound_per_minute: int = 60,
    ) -> None:
        self.frankfurter = frankfurter
        self.coinbase = coinbase
        self.stale_days = stale_days
        self._budget = _Budget(outbound_per_minute)
        self._answers = _TtlCache(2048)  # (base, quote, day) -> fx_rates.id
        self._failures = _TtlCache(512)  # (provider, base, quote) -> "down" | "unsupported"
        self._spot = _TtlCache(64)  # quote -> Quote

    def reset(self) -> None:
        for cache in (self._answers, self._failures, self._spot):
            cache.clear()

    # --- Daily references (stored)

    def daily(
        self,
        db: Session,
        base: str,
        quote: str,
        on: date | None,
        today: date,
        *,
        fresh: bool = False,
    ) -> RateView:
        """The reference rate for a day (or the latest), stored, or the best honest fallback.

        `fresh` (a person pressing Refresh) skips the half-hour answer cache for "latest" and asks
        the provider again, still within the outbound budget and the failure back-off.
        """
        requested = on or today
        key = (base, quote, on.isoformat() if on else "latest")
        cached = None if fresh and on is None else self._answers.get(key)
        if cached is not None:
            row = db.get(FxRate, cached)
            if row is not None:
                return _view(row, requested, self.stale_days, fallback=False)
        if on is not None:
            exact = db.scalar(
                select(FxRate).where(
                    FxRate.source == "provider",
                    FxRate.base == base,
                    FxRate.quote == quote,
                    FxRate.rate_date == on,
                )
            )
            if exact is not None:
                self._answers.put(key, exact.id, 24 * 3600)
                return _view(exact, requested, self.stale_days, fallback=False)

        failure = self._failures.get(("frankfurter", base, quote))
        if failure == "unsupported":
            raise ApiError(422, "fx_pair_unsupported", "No reference rate exists for this pair")
        if failure is None and self._budget.take():
            try:
                found = self.frankfurter.daily(base, quote, on, today)
            except PairUnsupportedError:
                self._failures.put(("frankfurter", base, quote), "unsupported", 24 * 3600)
                raise ApiError(
                    422, "fx_pair_unsupported", "No reference rate exists for this pair"
                ) from None
            except ProviderUnavailableError:
                self._failures.put(("frankfurter", base, quote), "down", 60)
            else:
                row = self._store(db, found)
                # Past days don't change; "today" and "latest" are rechecked twice an hour.
                final = on is not None and on < today - timedelta(days=1)
                self._answers.put(key, row.id, 24 * 3600 if final else 1800)
                return _view(row, requested, self.stale_days, fallback=False)

        older = db.scalar(
            select(FxRate)
            .where(
                FxRate.source == "provider",
                FxRate.base == base,
                FxRate.quote == quote,
                FxRate.rate_date <= requested,
            )
            .order_by(FxRate.rate_date.desc(), FxRate.retrieved_at.desc())
            .limit(1)
        )
        if older is None:
            raise unavailable()
        return _view(older, requested, self.stale_days, fallback=True)

    def cached_reference(self, db: Session, base: str, quote: str, on: date) -> FxRate | None:
        """The closest stored reference on or before a day, without any network call."""
        return db.scalar(
            select(FxRate)
            .where(
                FxRate.source == "provider",
                FxRate.base == base,
                FxRate.quote == quote,
                FxRate.rate_date <= on,
                FxRate.rate_date >= on - timedelta(days=self.stale_days),
            )
            .order_by(FxRate.rate_date.desc())
            .limit(1)
        )

    def _store(self, db: Session, found: Quote) -> FxRate:
        if found.rate_date is None:
            raise unavailable()
        values = {
            "base": found.base,
            "quote": found.quote,
            "rate": found.rate.quantize(RATE_PLACES, rounding=ROUND_HALF_UP),
            "rate_date": found.rate_date,
            "source": "provider",
            "provider": found.provider,
            "upstream": found.upstream,
        }
        db.execute(
            insert(FxRate)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["provider", "upstream", "base", "quote", "rate_date"],
                index_where=FxRate.source == "provider",
            )
        )
        row = db.scalar(
            select(FxRate).where(
                FxRate.source == "provider",
                FxRate.provider == found.provider,
                FxRate.upstream == found.upstream,
                FxRate.base == found.base,
                FxRate.quote == found.quote,
                FxRate.rate_date == found.rate_date,
            )
        )
        if row is None:
            raise unavailable()
        return row

    # --- Bitcoin spot (display only, never stored)

    def btc_spot(self, quote: str) -> Quote | None:
        cached = self._spot.get(quote)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        if self._failures.get(("coinbase", "BTC", quote)) is not None or not self._budget.take():
            return None
        try:
            found = self.coinbase.spot("BTC", quote)
        except PairUnsupportedError:
            self._failures.put(("coinbase", "BTC", quote), "unsupported", 24 * 3600)
            return None
        except ProviderUnavailableError:
            self._failures.put(("coinbase", "BTC", quote), "down", 60)
            return None
        self._spot.put(quote, found, 60)
        return found


# --- Conversion arithmetic (Decimal only)


def convert_minor(amount_minor: int, from_exp: int, to_exp: int, rate: Decimal) -> int:
    """round_half_up(amount * rate * 10^(to_exp - from_exp)), FINANCIAL_RULES.md §8.2."""
    exact = Decimal(amount_minor) * rate * (Decimal(10) ** (to_exp - from_exp))
    result = int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if result == 0:
        raise ApiError(422, "conversion_rounds_to_zero", "The converted amount rounds to zero")
    if result > MAX_MINOR:
        raise ApiError(422, "invalid_amount", "The converted amount is too large")
    return result


def implied_rate(original_minor: int, from_exp: int, posted_minor: int, to_exp: int) -> Decimal:
    """What the amount actually charged works out to, per unit of the original currency."""
    rate = (Decimal(posted_minor) / Decimal(10) ** to_exp) / (
        Decimal(original_minor) / Decimal(10) ** from_exp
    )
    rate = rate.quantize(RATE_PLACES, rounding=ROUND_HALF_UP)
    if rate <= 0:
        raise ApiError(422, "invalid_rate", "That rate is too small to record")
    return rate


def parse_manual_rate(text: str) -> Decimal:
    """A rate the person typed: canonical decimal, positive, at most 12 decimals, sensible size."""
    if not _MANUAL_RATE.match(text):
        raise ApiError(422, "invalid_rate", "Enter the rate as a plain decimal number")
    try:
        return check_rate(text)
    except ProviderUnavailableError:
        raise ApiError(422, "invalid_rate", "That rate is outside any sensible range") from None


def record_manual_rate(
    db: Session, user: User, base: str, quote: str, rate: Decimal, on: date
) -> FxRate:
    row = FxRate(
        base=base, quote=quote, rate=rate, rate_date=on, source="manual", entered_by=user.id
    )
    db.add(row)
    db.flush()
    return row


def deviation(rate: Decimal, reference: Decimal) -> Decimal:
    return abs(rate - reference) / reference


def now() -> datetime:
    return datetime.now(UTC)
