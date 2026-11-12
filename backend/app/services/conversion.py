"""Turning an amount entered in one currency into the amount posted in the account's (§4.6, §7).

The person chooses one of three ways, and the choice is recorded on the transaction:

- `reference`: original * the stored reference rate, rounded once; keeps the provider row.
- `manual_rate`: original * the rate they typed; records it as a manual rate row.
- `actual_amount`: exactly the amount they say was charged; the implied rate is stored for
  display, with the suggestion they saw, if any.

No network call happens here: a reference is chosen by the id of a row the preview stored. A
reference older than the freshness limit for the transaction's date needs `accept_stale`. A typed
rate or charged amount far from the reference is allowed (airport rates are real) but returned as a
warning and written to the audit log, never blocked.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import FxRate, User
from app.services import fx, money

METHODS = ("reference", "manual_rate", "actual_amount")


@dataclass(frozen=True)
class Conversion:
    """What the person chose in the conversion panel."""

    method: str
    rate_id: uuid.UUID | None = None
    rate: str | None = None
    account_amount: str | None = None
    accept_stale: bool = False


@dataclass
class Converted:
    posted_minor: int
    rate: Decimal
    method: str
    rate_id: uuid.UUID | None
    warnings: list[str] = field(default_factory=list)
    deviation: Decimal | None = None


def _reference_row(db: Session, rate_id: uuid.UUID, base: str, quote: str) -> FxRate:
    row = db.get(FxRate, rate_id)
    if row is None or row.source != "provider" or row.base != base or row.quote != quote:
        raise ApiError(422, "fx_rate_mismatch", "That rate doesn't match these currencies")
    return row


def apply(
    db: Session,
    gateway: fx.FxGateway,
    user: User,
    *,
    original_minor: int,
    original_currency: str,
    account_currency: str,
    on: date,
    choice: Conversion,
) -> Converted:
    if choice.method not in METHODS:
        raise ApiError(422, "validation_error", "Unknown conversion method")
    from_exp = money.currency(db, original_currency).exponent
    to_exp = money.currency(db, account_currency, for_new_records=False).exponent

    if choice.method == "reference":
        if choice.rate_id is None:
            raise ApiError(422, "validation_error", "A reference rate is required")
        row = _reference_row(db, choice.rate_id, original_currency, account_currency)
        if row.rate_date > on + timedelta(days=1):
            raise ApiError(422, "fx_rate_mismatch", "That rate is for a later day")
        if (on - row.rate_date).days > gateway.stale_days and not choice.accept_stale:
            raise ApiError(
                409,
                "fx_rate_stale",
                "This rate is older than the freshness limit; confirm it or enter your own",
                {"rate_date": row.rate_date.isoformat(), "limit_days": gateway.stale_days},
            )
        return Converted(
            posted_minor=fx.convert_minor(original_minor, from_exp, to_exp, row.rate),
            rate=row.rate,
            method="reference",
            rate_id=row.id,
        )

    reference = gateway.cached_reference(db, original_currency, account_currency, on)
    if choice.method == "manual_rate":
        if choice.rate is None:
            raise ApiError(422, "validation_error", "Enter a rate")
        rate = fx.parse_manual_rate(choice.rate)
        posted = fx.convert_minor(original_minor, from_exp, to_exp, rate)
        manual = fx.record_manual_rate(db, user, original_currency, account_currency, rate, on)
        result = Converted(posted, rate, "manual_rate", manual.id)
    else:
        if choice.account_amount is None:
            raise ApiError(422, "validation_error", "Enter the amount charged")
        posted, _ = money.parse(db, choice.account_amount, account_currency)
        if posted <= 0:
            raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
        rate = fx.implied_rate(original_minor, from_exp, posted, to_exp)
        shown = (
            _reference_row(db, choice.rate_id, original_currency, account_currency)
            if choice.rate_id
            else None
        )
        if shown is not None:
            reference = shown
        result = Converted(posted, rate, "actual_amount", shown.id if shown else None)

    if reference is not None:
        result.deviation = fx.deviation(result.rate, reference.rate)
        if result.deviation > fx.MANUAL_DEVIATION:
            result.warnings.append("rate_far_from_reference")
    return result
