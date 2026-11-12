"""This month's spending and income, per currency (FINANCIAL_RULES.md §16, OD-8).

Everything is read from the ledger, so the figures can't disagree with balances:

- **Spending** is the sum of lines on the user's `expense` ledger accounts dated in the month.
- **Income** is the (negated) sum of lines on `income` ledger accounts dated in the month.
- **Top categories** group those expense lines by their entry's category.
- **Trip shares** (§11.10): the person's allocated share of approved trip expenses dated in the
  month counts in spending ("Your share"); the cash they paid into trips from their accounts
  ("Amount you paid") and got back is shown beside it, and is never spending or income.

Transfers, opening balances, and adjustments post to asset, equity, and FX clearing accounts only,
so they can never appear here (INV-07, §4.4). A void or a correction reverses the original entry on
its original date and category, so a voided expense nets to zero in its month, and a corrected one
counts once, in its corrected month and category. Currencies are never added together.
"""

import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.db.models import User
from app.services import reporting

TOP_CATEGORIES = 5


@dataclass
class CurrencyMonth:
    currency: str
    expenses_minor: int = 0
    income_minor: int = 0
    transactions: int = 0
    top: list[tuple[uuid.UUID, int]] = field(default_factory=list)
    # Every category with spending, largest first; `top` is the first few.
    categories: list[tuple[uuid.UUID, int]] = field(default_factory=list)
    trip_shares_minor: int = 0  # part of expenses_minor: "Your share"
    trip_paid_minor: int = 0  # cash paid into trips from accounts: "Amount you paid"
    trip_received_minor: int = 0  # cash received back from trips

    @property
    def uncategorized_minor(self) -> int:
        """Spending with no category of its own, so the breakdown adds up to `expenses_minor`."""
        return self.expenses_minor - sum(amount for _, amount in self.categories)

    @property
    def has_trips(self) -> bool:
        return bool(self.trip_shares_minor or self.trip_paid_minor or self.trip_received_minor)


def month_bounds(month: date) -> tuple[date, date]:
    first = month.replace(day=1)
    return first, first.replace(day=monthrange(first.year, first.month)[1])


def month_summary(db: Session, user: User, month: date) -> list[CurrencyMonth]:
    """Home's month, from the same queries as Insights and Reports (FINANCIAL_RULES.md §16.1)."""
    first, last = month_bounds(month)
    scope = reporting.Scope(first, last)
    by_currency: dict[str, CurrencyMonth] = {}
    for code, flow in reporting.flows(db, user, scope).items():
        by_currency[code] = CurrencyMonth(
            code,
            expenses_minor=flow.expenses,
            income_minor=flow.income,
            transactions=0,
            trip_shares_minor=flow.trip_shares,
        )
    for code, moved in reporting.movements(db, user, scope).items():
        if moved.trips_out or moved.trips_in:
            item = by_currency.setdefault(code, CurrencyMonth(code))
            item.trip_paid_minor, item.trip_received_minor = moved.trips_out, moved.trips_in
    # Home counts income and expense entries, as before.
    counted = reporting.flows(db, user, reporting.Scope(first, last, types=("income", "expense")))
    for code, flow in counted.items():
        by_currency.setdefault(code, CurrencyMonth(code)).transactions = flow.transactions
    for total in reporting.by_category(db, user, scope):
        if total.kind == "expense" and total.category_id is not None and total.amount > 0:
            by_currency.setdefault(total.currency, CurrencyMonth(total.currency)).top.append(
                (total.category_id, total.amount)
            )
    for summary in by_currency.values():
        summary.top.sort(key=lambda item: (-item[1], str(item[0])))
        summary.categories = list(summary.top)
        del summary.top[TOP_CATEGORIES:]

    # Currencies with something to show, the busiest first.
    rows = [
        s
        for s in by_currency.values()
        if s.expenses_minor or s.income_minor or s.transactions or s.trip_paid_minor
        or s.trip_received_minor
    ]  # fmt: skip
    return sorted(rows, key=lambda s: (-s.transactions, s.currency))
