"""Exchange-rate preview and Amount Equivalent (API_SPEC.md §8).

Both are reads for the signed-in user, rate limited per client because they can reach a provider.
Changing equivalent favourites is a write, so it needs CSRF like any other change.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.orm import Session

from app.api.deps import rate_limited, require_active_user, require_writable_user
from app.api.v1.accounts import CurrencyCode, MoneyOut, _no_store
from app.api.v1.finance import gateway
from app.core.errors import ApiError
from app.core.limits import MONTH_PATTERN
from app.core.rate_limit import Limit
from app.db.models import User
from app.db.session import get_db
from app.services import equivalents, fx, money, summary
from app.services.accounts import today_for

router = APIRouter(tags=["fx"], dependencies=[Depends(_no_store)])
read_limit = Depends(rate_limited("fx.read", Limit(requests=60, window_seconds=60)))


class RateOut(BaseModel):
    id: uuid.UUID
    base: str
    quote: str
    rate: str  # 1 base = rate quote
    rate_date: date
    requested_date: date
    source: str
    provider: str | None
    upstream: str | None
    retrieved_at: datetime
    age_days: int
    is_stale: bool
    # The provider couldn't be reached and this is the newest stored rate instead.
    fallback: bool


class ConvertOut(BaseModel):
    # The reference rate; null when previewing a rate the person typed.
    rate: RateOut | None
    original: MoneyOut | None
    converted: MoneyOut | None


def _rate_out(view: fx.RateView) -> RateOut:
    return RateOut(
        id=view.id,
        base=view.base,
        quote=view.quote,
        rate=format(view.rate.normalize(), "f"),
        rate_date=view.rate_date,
        requested_date=view.requested_date,
        source=view.source,
        provider=view.provider,
        upstream=view.upstream,
        retrieved_at=view.retrieved_at,
        age_days=view.age_days,
        is_stale=view.is_stale,
        fallback=view.fallback,
    )


@router.get("/fx/convert", dependencies=[read_limit])
def convert(
    request: Request,
    from_currency: Annotated[CurrencyCode, Query(alias="from")],
    to_currency: Annotated[CurrencyCode, Query(alias="to")],
    amount: Annotated[str | None, Query(max_length=30)] = None,
    on: Annotated[date | None, Query(alias="date")] = None,
    rate: Annotated[str | None, Query(max_length=30)] = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> ConvertOut:
    """The reference rate for a day, and what an amount becomes at it. Nothing is recorded but
    the rate observation itself, which the save step then references by id. With `rate`, previews
    a rate the person typed instead, with no provider call (M5: the server does the arithmetic)."""
    if from_currency == to_currency:
        raise ApiError(422, "same_currency", "Choose two different currencies")
    source = money.currency(db, from_currency)
    target = money.currency(db, to_currency, for_new_records=False)
    today = today_for(user)
    if on is not None and on > today:
        raise ApiError(422, "date_in_future", "The date can't be in the future")
    manual = fx.parse_manual_rate(rate) if rate is not None else None
    view = (
        None
        if manual
        else gateway(request).daily(db, from_currency, to_currency, on or today, today)
    )
    applied = manual if manual is not None else view.rate if view else None
    original = converted = None
    if amount is not None and applied is not None:
        minor, _ = money.parse(db, amount, from_currency)
        if minor <= 0:
            raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
        result = fx.convert_minor(minor, source.exponent, target.exponent, applied)
        original = MoneyOut(amount=money.to_amount(minor, source.exponent), currency=from_currency)
        converted = MoneyOut(amount=money.to_amount(result, target.exponent), currency=to_currency)
    return ConvertOut(
        rate=_rate_out(view) if view else None, original=original, converted=converted
    )


class EquivalentOut(BaseModel):
    target: str
    unit: str
    status: str
    value: str | None
    unit_price: str | None
    kind: str | None
    provider: str | None
    upstream: str | None
    rate_date: date | None
    as_of: datetime | None
    is_stale: bool


class EquivalentsOut(BaseModel):
    amount: MoneyOut
    targets: list[str]
    items: list[EquivalentOut]
    disclaimer: str = "indicative"


@router.get("/equivalents", dependencies=[read_limit])
def get_equivalents(
    request: Request,
    amount: Annotated[str, Query(max_length=30)],
    currency: CurrencyCode,
    targets: Annotated[str | None, Query(max_length=120)] = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> EquivalentsOut:
    """Display only: never posts, never changes a balance, never records any holding."""
    info = money.currency(db, currency, for_new_records=False)
    minor, _ = money.parse(db, amount, currency)
    keys = targets.split(",") if targets else list(user.equivalent_targets)
    keys = equivalents.check_targets(db, [k.strip().upper() for k in keys if k.strip()])
    items = equivalents.compute(
        db, gateway(request), amount_minor=minor, currency=currency, targets=keys,
        today=today_for(user),
    )  # fmt: skip
    return EquivalentsOut(
        amount=MoneyOut(amount=money.to_amount(minor, info.exponent), currency=currency),
        targets=keys,
        items=[EquivalentOut(**item.__dict__) for item in items],
    )


class TargetsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[Annotated[str, StringConstraints(min_length=3, max_length=6)]]


class TargetsOut(BaseModel):
    targets: list[str]


@router.get("/equivalents/targets")
def get_targets(user: User = Depends(require_active_user)) -> TargetsOut:
    return TargetsOut(targets=list(user.equivalent_targets))


@router.put("/equivalents/targets", dependencies=[read_limit])
def put_targets(
    body: TargetsIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> TargetsOut:
    keys = equivalents.check_targets(db, [k.upper() for k in body.targets])
    user.equivalent_targets = keys
    db.flush()
    return TargetsOut(targets=keys)


# --- Home: this month's spending, approximately, in another currency (M6.1, display only)


class SpendingPartOut(BaseModel):
    currency: str
    spent: MoneyOut  # authoritative: the ledger's own subtotal
    status: str  # "same", "ok", "unavailable"
    equivalent: MoneyOut | None  # approximate, at the latest daily reference
    rate: str | None  # 1 target = rate (this currency)
    reason: str | None
    provider: str | None
    upstream: str | None
    rate_date: date | None
    retrieved_at: datetime | None
    is_stale: bool


class MonthSideOut(BaseModel):
    parts: list[SpendingPartOut]
    # The sum of the parts; null unless every part has a rate (never shown incomplete).
    combined: MoneyOut | None
    complete: bool
    is_stale: bool


class SpendingEquivalentOut(MonthSideOut):
    """The month's spending, with its income beside it in `received`."""

    month: str
    target: str
    received: MonthSideOut


@router.get("/summary/equivalent", dependencies=[read_limit])
def spending_equivalent(
    request: Request,
    target: CurrencyCode,
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    refresh: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> SpendingEquivalentOut:
    """This month's spending and income (Home's figures) at today's reference rate in one currency.

    Approximate and display only: no transaction, journal line, balance or stored rate on a
    transaction changes, and the result isn't saved. Only the reference rate is cached.
    """
    money.currency(db, target)  # 422 unsupported_currency
    today = today_for(user)
    first = date.fromisoformat(f"{month}-01") if month else today.replace(day=1)
    rows = summary.month_summary(db, user, first)
    exponents = money.exponents(db, {r.currency for r in rows})

    def approx(value: Decimal | None) -> MoneyOut | None:
        return MoneyOut(amount=format(value, "f"), currency=target) if value is not None else None

    def side(field: str) -> MonthSideOut:
        counted = [r for r in rows if getattr(r, field)]
        parts, combined = equivalents.spending_in(
            db,
            gateway(request),
            subtotals=[(r.currency, getattr(r, field)) for r in counted],
            target=target,
            today=today,
            fresh=refresh,
        )
        return MonthSideOut(
            parts=[
                SpendingPartOut(
                    currency=p.currency,
                    spent=MoneyOut(
                        amount=money.to_amount(p.spent_minor, exponents[p.currency]),
                        currency=p.currency,
                    ),
                    status=p.status,
                    equivalent=approx(p.value),
                    rate=format(p.rate.normalize(), "f") if p.rate is not None else None,
                    reason=p.reason,
                    provider=p.provider,
                    upstream=p.upstream,
                    rate_date=p.rate_date,
                    retrieved_at=p.as_of,
                    is_stale=p.is_stale,
                )
                for p in parts
            ],
            combined=approx(combined),
            complete=combined is not None,
            is_stale=any(p.is_stale for p in parts),
        )

    spent = side("expenses_minor")
    return SpendingEquivalentOut(
        month=first.strftime("%Y-%m"),
        target=target,
        **spent.model_dump(),
        received=side("income_minor"),
    )
