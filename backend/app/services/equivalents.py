"""Amount Equivalent: what an amount is roughly worth elsewhere (FINANCIAL_RULES.md §7a).

DISPLAY ONLY. Nothing here writes a balance, a transaction or a journal line, or records that the
person holds gold or Bitcoin. The quotes are indicative: daily central-bank references for
currencies and gold, an indicative spot price for Bitcoin. None of them is an executable price.

Targets are keys:

- `USD`, `EUR`, ...: an active ISO 4217 currency, from the Frankfurter daily reference, shown to
  its minor unit.
- `XAU_G` and `XAU_OZ`: grams or troy ounces of fine gold (XAU), from Frankfurter's daily XAU
  price per troy ounce; 4 significant figures, at most 3 or 4 decimals.
- `BTC`: bitcoin, from Coinbase's indicative spot price; 4 significant figures, at most 8 decimals.

A troy ounce is exactly 31.1034768 g. XAU is always fine gold; other purities aren't offered.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.services import fx, money

ASSETS = {"XAU_G": ("XAU", "gram", 3), "XAU_OZ": ("XAU", "troy_ounce", 4), "BTC": ("BTC", "btc", 8)}
_KEY = re.compile(r"^([A-Z]{3}|XAU_G|XAU_OZ)$")
MAX_TARGETS = 12
SIGNIFICANT = 4


@dataclass(frozen=True)
class Equivalent:
    target: str
    unit: str  # "currency", "gram", "troy_ounce", "btc"
    status: str  # "ok" or "unavailable"
    value: str | None = None
    # How much one unit of the target costs in the amount's currency: "1 g of gold = 6,592.31 TRY".
    unit_price: str | None = None
    kind: str | None = None  # "daily_reference" or "spot"
    provider: str | None = None
    upstream: str | None = None
    rate_date: date | None = None
    as_of: datetime | None = None
    is_stale: bool = False


def check_targets(db: Session, targets: list[str]) -> list[str]:
    """Distinct, known keys, in the order given. 422 otherwise."""
    seen: list[str] = []
    for key in targets:
        if not _KEY.match(key) and key != "BTC":
            raise ApiError(422, "invalid_target", "Unknown equivalent", {"target": key[:12]})
        if key not in ASSETS:
            money.currency(db, key)  # 422 unsupported_currency
        if key not in seen:
            seen.append(key)
    if len(seen) > MAX_TARGETS:
        raise ApiError(422, "too_many_targets", "Choose at most 12 equivalents")
    return seen


def significant(value: Decimal, max_places: int) -> Decimal:
    """Rounded to 4 significant figures (no false precision), with at most `max_places` decimals."""
    if value == 0:
        return Decimal(0)
    places = SIGNIFICANT - 1 - value.adjusted()
    places = max(0, min(places, max_places))
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _plain(value: Decimal) -> str:
    return format(value, "f")


def compute(
    db: Session,
    gateway: fx.FxGateway,
    *,
    amount_minor: int,
    currency: str,
    targets: list[str],
    today: date,
) -> list[Equivalent]:
    source = money.currency(db, currency, for_new_records=False)
    amount = Decimal(amount_minor).scaleb(-source.exponent)
    out: list[Equivalent] = []
    for key in targets:
        if key == currency:
            continue
        if key == "BTC":
            out.append(_bitcoin(gateway, amount, currency))
            continue
        base, unit, places = ASSETS.get(key, (key, "currency", 0))
        try:
            reference = gateway.daily(db, base, currency, None, today)
        except ApiError:
            out.append(Equivalent(target=key, unit=unit, status="unavailable"))
            continue
        price = reference.rate  # 1 base = price (amount's currency)
        if base == "XAU" and unit == "gram":
            price = price / fx.TROY_OUNCE_GRAMS
        if unit == "currency":
            exponent = money.currency(db, key, for_new_records=False).exponent
            value = (amount / price).quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP)
        else:
            value = significant(amount / price, places)
        out.append(
            Equivalent(
                target=key,
                unit=unit,
                status="ok",
                value=_plain(value),
                unit_price=_plain(
                    price.quantize(Decimal(1).scaleb(-source.exponent), rounding=ROUND_HALF_UP)
                ),
                kind="daily_reference",
                provider=reference.provider,
                upstream=reference.upstream,
                rate_date=reference.rate_date,
                as_of=reference.retrieved_at,
                is_stale=reference.is_stale or reference.fallback,
            )
        )
    return out


def _bitcoin(gateway: fx.FxGateway, amount: Decimal, currency: str) -> Equivalent:
    quote = gateway.btc_spot(currency)
    if quote is None:
        return Equivalent(target="BTC", unit="btc", status="unavailable")
    return Equivalent(
        target="BTC",
        unit="btc",
        status="ok",
        value=_plain(significant(amount / quote.rate, 8)),
        unit_price=_plain(quote.rate.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        kind="spot",
        provider=quote.provider,
        upstream=quote.upstream,
        as_of=quote.observed_at,
    )


# --- Home: this month's spending, roughly, in one currency (M6.1)


@dataclass(frozen=True)
class SpendingPart:
    """One currency's authoritative subtotal and its approximate value in the target."""

    currency: str
    spent_minor: int
    status: str  # "same" (already the target), "ok", or "unavailable"
    value: Decimal | None = None  # in the target, rounded to its minor unit
    rate: Decimal | None = None  # 1 target = rate (this currency)
    reason: str | None = None  # why it's unavailable: "fx_unavailable", "fx_pair_unsupported"
    provider: str | None = None
    upstream: str | None = None
    rate_date: date | None = None
    as_of: datetime | None = None
    is_stale: bool = False


def spending_in(
    db: Session,
    gateway: fx.FxGateway,
    *,
    subtotals: list[tuple[str, int]],
    target: str,
    today: date,
    fresh: bool = False,
) -> tuple[list[SpendingPart], Decimal | None]:
    """Each currency's spending at the latest daily reference, and their sum when every part has a
    rate. DISPLAY ONLY: today's reference, never the rates the expenses were posted at, and
    nothing is stored except the reference rate itself (the FX cache). Decimal throughout; each
    part is rounded half up to the target's minor unit and the combined figure is the sum of the
    rounded parts, so the parts shown always add up to it. One missing rate means no combined
    figure: an incomplete total is never shown as complete.
    """
    exponent = money.currency(db, target, for_new_records=False).exponent
    unit = Decimal(1).scaleb(-exponent)
    parts: list[SpendingPart] = []
    for code, minor in subtotals:
        amount = Decimal(minor).scaleb(-money.currency(db, code, for_new_records=False).exponent)
        if code == target:
            parts.append(SpendingPart(code, minor, "same", value=amount.quantize(unit)))
            continue
        try:
            reference = gateway.daily(db, target, code, None, today, fresh=fresh)
        except ApiError as error:
            parts.append(SpendingPart(code, minor, "unavailable", reason=error.code))
            continue
        parts.append(
            SpendingPart(
                code,
                minor,
                "ok",
                value=(amount / reference.rate).quantize(unit, rounding=ROUND_HALF_UP),
                rate=reference.rate,
                provider=reference.provider,
                upstream=reference.upstream,
                rate_date=reference.rate_date,
                as_of=reference.retrieved_at,
                is_stale=reference.is_stale or reference.fallback,
            )
        )
    complete = bool(parts) and all(p.value is not None for p in parts)
    combined = (
        sum((p.value for p in parts if p.value is not None), Decimal(0)) if complete else None
    )
    return parts, combined
