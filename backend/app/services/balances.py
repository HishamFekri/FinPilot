"""Total Balance, in a currency the person picks (FINANCIAL_RULES.md §7c).

DISPLAY ONLY, like the Amount Equivalent (§7a): every active account's ledger balance, per currency,
then each currency converted at the latest stored daily reference rate. Nothing is posted, stored
or recalculated. Stored balances and past transactions never change when rates do. One missing rate
means no combined figure: an incomplete total is never shown as complete. Debts, installment plans,
and things owned are never read here, so they can't be mixed in.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import FinancialAccount, User
from app.services import fx, ledger, money


@dataclass(frozen=True)
class Rate:
    currency: str
    status: str  # same, ok, unavailable
    rate: Decimal | None = None  # units of `currency` per one unit of the target
    rate_date: date | None = None
    provider: str | None = None
    upstream: str | None = None
    retrieved_at: datetime | None = None
    is_stale: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class AccountLine:
    account: FinancialAccount
    balance_minor: int
    equivalent: Decimal | None


@dataclass
class Group:
    """One figure per currency, and its conversion."""

    by_currency: dict[str, int] = field(default_factory=dict)
    converted: dict[str, Decimal | None] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return all(v is not None for v in self.converted.values())

    @property
    def combined(self) -> Decimal | None:
        if not self.complete:
            return None
        return sum((v for v in self.converted.values() if v is not None), Decimal(0))


def _rate(
    db: Session, gateway: fx.FxGateway, target: str, code: str, today: date, fresh: bool
) -> Rate:
    if code == target:
        return Rate(code, "same")
    try:
        found = gateway.daily(db, target, code, None, today, fresh=fresh)
    except ApiError as error:
        return Rate(code, "unavailable", reason=error.code)
    return Rate(
        code,
        "ok",
        rate=found.rate,
        rate_date=found.rate_date,
        provider=found.provider,
        upstream=found.upstream,
        retrieved_at=found.retrieved_at,
        is_stale=found.is_stale or found.fallback,
    )


def _convert(db: Session, minor: int, code: str, rate: Rate, unit: Decimal) -> Decimal | None:
    amount = Decimal(minor).scaleb(-money.currency(db, code, for_new_records=False).exponent)
    if rate.status == "same":
        return amount.quantize(unit)
    if rate.rate is None:
        return None
    return (amount / rate.rate).quantize(unit, rounding=ROUND_HALF_UP)


def balances_only(
    db: Session,
    user: User,
    gateway: fx.FxGateway,
    *,
    target: str,
    today: date,
    fresh: bool = False,
) -> tuple[dict[str, Rate], list[AccountLine], Group]:
    """Active accounts only."""
    money.currency(db, target)
    unit = Decimal(1).scaleb(-money.currency(db, target, for_new_records=False).exponent)
    accounts = list(
        db.scalars(
            select(FinancialAccount)
            .where(FinancialAccount.user_id == user.id, FinancialAccount.status == "active")
            .order_by(FinancialAccount.created_at)
        )
    )
    balances = ledger.balances(db, user.id, [a.id for a in accounts])
    codes = sorted({a.currency for a in accounts})
    rates = {c: _rate(db, gateway, target, c, today, fresh) for c in codes}
    group = Group()
    for a in accounts:
        group.by_currency[a.currency] = group.by_currency.get(a.currency, 0) + balances[a.id]
    group.converted = {c: _convert(db, m, c, rates[c], unit) for c, m in group.by_currency.items()}
    lines = [
        AccountLine(
            a, balances[a.id], _convert(db, balances[a.id], a.currency, rates[a.currency], unit)
        )
        for a in accounts
    ]
    return rates, lines, group
