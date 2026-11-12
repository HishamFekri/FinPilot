"""Total Balance in a chosen currency (FINANCIAL_RULES.md §7c). Display only."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_active_user
from app.api.v1.accounts import CurrencyCode, MoneyOut, _no_store
from app.api.v1.finance import gateway
from app.api.v1.fx import read_limit
from app.db.models import User
from app.db.session import get_db
from app.services import balances, money
from app.services.accounts import today_for

router = APIRouter(tags=["balances"], dependencies=[Depends(_no_store)])


class RateOut(BaseModel):
    currency: str
    status: str
    rate: str | None
    rate_date: date | None
    provider: str | None
    upstream: str | None
    retrieved_at: datetime | None
    is_stale: bool
    reason: str | None


class AccountValueOut(BaseModel):
    id: uuid.UUID
    name: str | None
    system_key: str | None
    type: str
    currency: str
    balance: MoneyOut
    equivalent: MoneyOut | None
    is_default: bool


class GroupOut(BaseModel):
    by_currency: list[MoneyOut]
    # Each currency's figure in the target currency (null where no rate is available).
    converted: list[MoneyOut | None]
    combined: MoneyOut | None
    complete: bool


class TotalBalanceOut(BaseModel):
    """Active accounts only: never debts, installment plans, or anything owed."""

    target: str
    accounts: list[AccountValueOut]
    balances: GroupOut
    missing_rates: list[str]
    is_stale: bool
    rates: list[RateOut]


@router.get("/balances/total", dependencies=[read_limit])
def total_balance(
    request: Request,
    target: CurrencyCode,
    refresh: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> TotalBalanceOut:
    """Each currency's exact total, and an approximate combined figure at the latest reference
    rate. Display only: nothing is stored or posted."""
    rates, lines, group = balances.balances_only(
        db, user, gateway(request), target=target, today=today_for(user), fresh=refresh
    )

    def approx(value: Decimal | None) -> MoneyOut | None:
        return MoneyOut(amount=format(value, "f"), currency=target) if value is not None else None

    def exact(minor: int, code: str) -> MoneyOut:
        exponent = money.currency(db, code, for_new_records=False).exponent
        return MoneyOut(amount=money.to_amount(minor, exponent), currency=code)

    codes = sorted(group.by_currency)
    return TotalBalanceOut(
        target=target,
        accounts=[
            AccountValueOut(
                id=line.account.id,
                name=line.account.name,
                system_key=line.account.system_key,
                type=line.account.type,
                currency=line.account.currency,
                balance=exact(line.balance_minor, line.account.currency),
                equivalent=approx(line.equivalent),
                is_default=line.account.id == user.default_account_id,
            )
            for line in lines
        ],
        balances=GroupOut(
            by_currency=[exact(group.by_currency[c], c) for c in codes],
            converted=[approx(group.converted[c]) for c in codes],
            combined=approx(group.combined),
            complete=group.complete,
        ),
        missing_rates=sorted(c for c, r in rates.items() if r.status == "unavailable"),
        is_stale=any(r.is_stale for r in rates.values()),
        rates=[
            RateOut(
                currency=r.currency,
                status=r.status,
                rate=format(r.rate.normalize(), "f") if r.rate is not None else None,
                rate_date=r.rate_date,
                provider=r.provider,
                upstream=r.upstream,
                retrieved_at=r.retrieved_at,
                is_stale=r.is_stale,
                reason=r.reason,
            )
            for r in rates.values()
        ],
    )
