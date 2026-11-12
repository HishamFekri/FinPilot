"""The money calendar: a month, one currency at a time (PRD §3.4, FINANCIAL_RULES.md §16).

Every figure comes from `reporting` (spending includes allocated trip shares, refunds reduce
it, like everywhere else) and from the recurring engine's occurrences. Days are the person's own
calendar dates (`effective_date`, set in their time zone), so a day boundary is theirs, not UTC.
Currencies are never added together: the calendar shows one at a time.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import RecurringOccurrence, RecurringRule, Transaction, User
from app.services import recurring, reporting
from app.services.budgets import month_end


@dataclass
class Day:
    day: date
    income: int = 0
    expenses: int = 0
    transactions: int = 0
    trip_shares: list[reporting.ShareLine] = field(default_factory=list)


@dataclass
class Month:
    first: date
    last: date
    currency: str | None
    currencies: list[str]
    days: dict[date, Day]
    occurrences: list[tuple[RecurringOccurrence, RecurringRule]]
    today: date


def month(
    db: Session,
    user: User,
    first: date,
    *,
    currency: str | None = None,
    account_id: uuid.UUID | None = None,
) -> Month:
    first = first.replace(day=1)
    last = month_end(first)
    today = recurring.ensure(db, user)
    accounts = (account_id,) if account_id else ()
    flows = reporting.flows(db, user, reporting.Scope(first, last, accounts))
    codes = sorted(flows, key=lambda c: (-(flows[c].expenses + flows[c].income), c))
    code = currency or (codes[0] if codes else None)
    days = {first + timedelta(days=i): Day(first + timedelta(days=i))
            for i in range((last - first).days + 1)}  # fmt: skip
    if code is not None:
        scope = reporting.Scope(first, last, accounts, currencies=(code,))
        for point in reporting.series(db, user, scope, "day"):
            days[point.bucket].income = point.income
            days[point.bucket].expenses = point.expenses
        for share in reporting.scope_shares(db, user, scope):
            days[share.expense_date].trip_shares.append(share)
        counts = select(Transaction.effective_date, func.count()).where(
            Transaction.user_id == user.id,
            Transaction.status == "posted",
            Transaction.currency == code,
            Transaction.effective_date >= first,
            Transaction.effective_date <= last,
        )
        if account_id:
            counts = counts.where(
                (Transaction.account_id == account_id)
                | (Transaction.counter_account_id == account_id)
            )
        for day, number in db.execute(counts.group_by(Transaction.effective_date)):
            days[day].transactions = int(number)
    pairs = [
        (o, r)
        for o, r in recurring.occurrences(db, user, date_from=first, date_to=last)
        if (code is None or o.currency == code)
        and (account_id is None or o.account_id == account_id)
    ]
    return Month(first, last, code, codes if code in codes or code is None else [code, *codes],
                 days, pairs, today)  # fmt: skip


# --- Subscriptions: recurring rules marked as such (no second scheduler)


@dataclass
class CurrencyCost:
    currency: str
    monthly: int = 0  # monthly rules, plus yearly ones / 12 (rounded half up per rule)
    annual: int = 0  # yearly rules, plus monthly ones x 12
    variable: int = 0  # subscriptions with no fixed amount: not in the totals
    approximate: bool = False  # True when a yearly amount was spread over 12 months


def costs(rules: list[RecurringRule]) -> list[CurrencyCost]:
    """Active subscriptions' cost per currency, never converted or added across currencies."""
    out: dict[str, CurrencyCost] = defaultdict(lambda: CurrencyCost(""))
    for rule in rules:
        if rule.status != "active":
            continue
        item = out[rule.currency]
        item.currency = rule.currency
        if rule.amount_minor is None:
            item.variable += 1
            continue
        if rule.frequency == "monthly":
            item.monthly += rule.amount_minor
            item.annual += rule.amount_minor * 12
        else:
            share, rest = divmod(rule.amount_minor, 12)
            item.monthly += share + (1 if rest * 2 >= 12 else 0)
            item.annual += rule.amount_minor
            item.approximate = item.approximate or rest != 0
    return sorted(out.values(), key=lambda c: c.currency)


def subscriptions(db: Session, user: User) -> list[RecurringRule]:
    return list(
        db.scalars(
            select(RecurringRule)
            .where(RecurringRule.user_id == user.id, RecurringRule.is_subscription.is_(True))
            .order_by(RecurringRule.status, RecurringRule.name)
        )
    )
