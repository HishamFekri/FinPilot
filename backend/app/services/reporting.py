"""The one place financial figures are calculated (FINANCIAL_RULES.md §16.1, §16.2, §16.2a).

Home, Budgets, Insights, Reports, the PDF and the CSV all call these functions; none recalculates.
Everything reads the immutable journal, so a figure can never disagree with a balance:

- **Spending** = the sum of lines on `expense` ledger accounts, plus the person's allocated
  **trip shares** (§11.10, OD-17); **income** = the negated sum of lines on `income` ledger
  accounts. Transfers, adjustments, opening balances, debts and trip cash (`trip_*` types, which
  post to Trip Clearing) post to asset, equity, clearing and debt accounts only, so they can never
  be spending or income. A reimbursement from a friend is therefore never income.
- **Trip shares** come from the trip records, never from the ledger (option A, OD-16): the
  person's `base_share_minor` on each expense's effective version (approved, not a cancellation),
  dated by its `expense_date`, in the trip's base currency (never converted), in the person's own
  category with the same system key. A correction moves it to its new date, amount and category;
  a dispute changes nothing. The view filter `trip_allocation` (include, the default; exclude;
  only) applies to Insights and Reports; budgets always include shares. A share has no account,
  so an account filter leaves shares out; a type filter keeps them only with `expense`.
- A void or correction reverses the original entry on its original date and category: a voided
  expense nets to zero, and a corrected one counts once, in its corrected period and category.
- A foreign-currency purchase posts only in the account's currency (§4.6); its original amount is a
  memo on the transaction and is never added, so nothing is counted twice.
- **Currencies are never added together.** Every result is per currency.
- **Cash movements** (the balance identity, §16.2) read lines on the asset accounts of the chosen
  accounts, grouped by the transaction's type. Debts add one term the original identity predates:
  money borrowed or repaid to you arrives (`debts_in`), money lent or repaid leaves (`debts_out`).

Dates are business dates (`effective_date`) in the person's time zone (§3): a period is a pair of
plain dates, inclusive.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Category,
    Debt,
    ExpenseShare,
    InstallmentPayment,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    RecurringOccurrence,
    RecurringRule,
    Transaction,
    Trip,
    TripExpenseVersion,
    User,
)

FLOW_TYPES = ("income", "expense")
TRIP_CASH_TYPES = ("trip_expense_payment", "trip_settlement_paid", "trip_settlement_received")
ALLOCATION_VIEWS = ("include", "exclude", "only")


def _int(value: Any) -> int:
    """A SQL sum (a Decimal from PostgreSQL's numeric, or None) as exact integer minor units."""
    return int(value) if value is not None else 0


def _uuid(value: Any) -> uuid.UUID | None:
    return value if isinstance(value, uuid.UUID) else None


def _as_date(value: Any) -> date:
    return value.date() if hasattr(value, "date") and not isinstance(value, date) else value  # type: ignore[no-any-return]


@dataclass(frozen=True)
class Scope:
    """What a figure covers. Empty tuples mean "all"."""

    date_from: date
    date_to: date
    account_ids: tuple[uuid.UUID, ...] = ()
    category_ids: tuple[uuid.UUID, ...] = ()
    types: tuple[str, ...] = ()
    currencies: tuple[str, ...] = ()
    trip_allocation: str = "include"  # §11.10: include, exclude, or only

    @property
    def balances_applicable(self) -> bool:
        """A balance has no category or type (§16.3): with those filters, balances are hidden."""
        return not self.category_ids and not self.types

    def previous(self) -> "Scope":
        """The period to compare with.

        A period starting on the 1st compares with the same days of the month before (1-7 Nov
        with 1-7 Oct; all of October with all of September), cut at that month's end. Any other
        period compares with the same number of days immediately before it.
        """
        days = (self.date_to - self.date_from).days + 1
        if self.date_from.day == 1:
            before = self.date_from - timedelta(days=1)  # the end of the month before
            if self.date_to == month_end_of(self.date_to):  # whole months: the same number
                months = (self.date_to.year - self.date_from.year) * 12 + (
                    self.date_to.month - self.date_from.month
                )
                first = before.replace(day=1)
                for _ in range(months):
                    first = (first - timedelta(days=1)).replace(day=1)
                return self._with(first, before)
            if (self.date_to.year, self.date_to.month) == (
                self.date_from.year,
                self.date_from.month,
            ):
                start = before.replace(day=1)  # month to date: the same days, a month earlier
                return self._with(start, min(start + timedelta(days=days - 1), before))
        return self._with(self.date_from - timedelta(days=days), before_start(self.date_from))

    def _with(self, start: date, end: date) -> "Scope":
        return Scope(
            start,
            end,
            self.account_ids,
            self.category_ids,
            self.types,
            self.currencies,
            self.trip_allocation,
        )


def _entry_filters(query: Any, user: User, scope: Scope) -> Any:
    """Restricts journal lines to the scope's entries: owner, period, currency, category, type,
    and (through their asset lines) the chosen accounts."""
    query = query.where(
        JournalLine.user_id == user.id,
        JournalLine.effective_date >= scope.date_from,
        JournalLine.effective_date <= scope.date_to,
    )
    if scope.currencies:
        query = query.where(JournalLine.currency.in_(scope.currencies))
    if scope.category_ids:
        query = query.where(JournalEntry.category_id.in_(scope.category_ids))
    if scope.types:
        query = query.where(
            JournalEntry.transaction_id.in_(
                select(Transaction.id).where(
                    Transaction.user_id == user.id, Transaction.type.in_(scope.types)
                )
            )
        )
    if scope.account_ids:
        other = JournalLine.__table__.alias("asset_line")
        asset = LedgerAccount.__table__.alias("asset_account")
        query = query.where(
            JournalEntry.id.in_(
                select(other.c.entry_id)
                .join(asset, asset.c.id == other.c.ledger_account_id)
                .where(asset.c.financial_account_id.in_(scope.account_ids))
            )
        )
    return query


# --- Trip shares (§11.10)


@dataclass(frozen=True)
class ShareLine:
    """One allocated share: what a trip expense cost this person, in the trip's base currency."""

    currency: str
    expense_date: date
    category_key: str
    category_id: uuid.UUID | None
    trip_id: uuid.UUID
    trip_name: str
    expense_id: uuid.UUID
    version_id: uuid.UUID
    description: str
    amount: int


def category_ids_by_key(db: Session, user_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """The person's own expense category for each system key (a trip keeps only the key)."""
    return {
        str(key): cid
        for key, cid in db.execute(
            select(Category.system_key, Category.id).where(
                Category.user_id == user_id,
                Category.kind == "expense",
                Category.system_key.is_not(None),
            )
        )
    }


def share_lines(
    db: Session,
    user: User,
    date_from: date,
    date_to: date,
    currencies: tuple[str, ...] = (),
    category_ids: tuple[uuid.UUID | None, ...] = (),
) -> list[ShareLine]:
    """The person's shares of effective trip expense versions dated in the period.

    Pending, rejected, superseded and cancelled versions never count. Membership doesn't matter:
    someone who has left a trip still had their share (§11.10).
    """
    keys = category_ids_by_key(db, user.id)
    query = (
        select(ExpenseShare.base_share_minor, TripExpenseVersion, Trip.name)
        .join(TripExpenseVersion, TripExpenseVersion.id == ExpenseShare.version_id)
        .join(Trip, Trip.id == ExpenseShare.trip_id)
        .where(
            ExpenseShare.user_id == user.id,
            ExpenseShare.base_share_minor != 0,
            TripExpenseVersion.state == "approved",
            TripExpenseVersion.kind != "cancellation",
            TripExpenseVersion.expense_date >= date_from,
            TripExpenseVersion.expense_date <= date_to,
        )
        .order_by(TripExpenseVersion.expense_date, TripExpenseVersion.id)
    )
    if currencies:
        query = query.where(TripExpenseVersion.base_currency.in_(currencies))
    out = []
    for amount, version, trip_name in db.execute(query):
        category_id = keys.get(version.category_key)
        if category_ids and category_id not in category_ids:
            continue
        out.append(
            ShareLine(
                currency=str(version.base_currency),
                expense_date=version.expense_date,
                category_key=version.category_key,
                category_id=category_id,
                trip_id=version.trip_id,
                trip_name=str(trip_name),
                expense_id=version.expense_id,
                version_id=version.id,
                description=version.description,
                amount=int(amount),
            )
        )
    return out


def shares_apply(scope: Scope) -> bool:
    """Whether a view's spending includes trip shares."""
    if scope.trip_allocation == "exclude" or scope.account_ids:
        return False
    return not scope.types or "expense" in scope.types


def scope_shares(db: Session, user: User, scope: Scope) -> list[ShareLine]:
    if not shares_apply(scope):
        return []
    return share_lines(
        db, user, scope.date_from, scope.date_to, scope.currencies, scope.category_ids
    )


def _ledger_spending(scope: Scope) -> bool:
    return scope.trip_allocation != "only"


# --- Income and spending


@dataclass
class Flows:
    currency: str
    income: int = 0
    expenses: int = 0  # spending: non-trip ledger spending plus trip shares, per the view
    transactions: int = 0
    trip_shares: int = 0  # the part of `expenses` that is allocated trip shares

    @property
    def non_trip_expenses(self) -> int:
        return self.expenses - self.trip_shares

    @property
    def net(self) -> int:
        """Income minus spending. Transfers, debts, adjustments and trip cash aren't in either."""
        return self.income - self.expenses


def flows(db: Session, user: User, scope: Scope) -> dict[str, Flows]:
    query = (
        select(LedgerAccount.kind, JournalLine.currency, func.sum(JournalLine.amount_minor))
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(LedgerAccount.kind.in_(FLOW_TYPES))
        .group_by(LedgerAccount.kind, JournalLine.currency)
    )
    out: dict[str, Flows] = {}
    for kind, code, total in db.execute(_entry_filters(query, user, scope)):
        item = out.setdefault(str(code), Flows(str(code)))
        if kind == "expense":
            if _ledger_spending(scope):
                item.expenses = _int(total)
        else:
            item.income = -_int(total)
    for code, number in db.execute(_count_query(user, scope, group_currency=True)):
        out.setdefault(str(code), Flows(str(code))).transactions = _int(number)
    for share in scope_shares(db, user, scope):
        item = out.setdefault(share.currency, Flows(share.currency))
        item.expenses += share.amount
        item.trip_shares += share.amount
    return out


def _count_query(user: User, scope: Scope, *, group_currency: bool) -> Any:
    """Posted transactions in the period and filters (voided ones aren't counted, §16.3)."""
    columns = [Transaction.currency, func.count()] if group_currency else [func.count()]
    query = select(*columns).where(
        Transaction.user_id == user.id,
        Transaction.status == "posted",
        Transaction.effective_date >= scope.date_from,
        Transaction.effective_date <= scope.date_to,
    )
    if scope.types:
        query = query.where(Transaction.type.in_(scope.types))
    if scope.currencies:
        query = query.where(Transaction.currency.in_(scope.currencies))
    if scope.category_ids:
        query = query.where(Transaction.category_id.in_(scope.category_ids))
    if scope.account_ids:
        query = query.where(
            (Transaction.account_id.in_(scope.account_ids))
            | (Transaction.counter_account_id.in_(scope.account_ids))
        )
    if group_currency:
        query = query.group_by(Transaction.currency)
    return query


@dataclass(frozen=True)
class CategoryTotal:
    currency: str
    category_id: uuid.UUID | None
    kind: str  # "expense" or "income"
    amount: int


def by_category(db: Session, user: User, scope: Scope) -> list[CategoryTotal]:
    """Spending (and income) per category and currency, largest first. Zero totals are dropped."""
    query = (
        select(
            JournalLine.currency,
            JournalEntry.category_id,
            LedgerAccount.kind,
            func.sum(JournalLine.amount_minor),
        )
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(LedgerAccount.kind.in_(FLOW_TYPES))
        .group_by(JournalLine.currency, JournalEntry.category_id, LedgerAccount.kind)
    )
    totals: dict[tuple[str, uuid.UUID | None, str], int] = defaultdict(int)
    for code, category_id, kind, total in db.execute(_entry_filters(query, user, scope)):
        if kind == "expense" and not _ledger_spending(scope):
            continue
        totals[(str(code), _uuid(category_id), str(kind))] += _int(total) * (
            1 if kind == "expense" else -1
        )
    for share in scope_shares(db, user, scope):
        totals[(share.currency, share.category_id, "expense")] += share.amount
    out = [CategoryTotal(c, cid, k, amount) for (c, cid, k), amount in totals.items() if amount]
    return sorted(out, key=lambda c: (c.currency, c.kind, -c.amount, str(c.category_id)))


# --- Day by day, classified (Spending Intelligence, §16.6)


@dataclass(frozen=True)
class DayLine:
    """Income or spending on one day, in one category and currency.

    `klass` splits spending without overlap, so the parts always add up to the total:

    - `subscription`, `scheduled`: confirmed from a recurring rule (subscriptions are marked rules);
      also installment interest and fees.
    - `financed`: the price of a purchase made on an installment plan, once, when it was bought.
      Repaying its principal is a debt payment, which is never spending.
    - `trip`: an allocated trip share.
    - `variable`: everything else. Decided per transaction, never per category.
    """

    currency: str
    day: date
    category_id: uuid.UUID | None
    kind: str  # "expense" or "income"
    klass: str
    amount: int
    trip_id: uuid.UUID | None = None
    trip_name: str | None = None


def day_lines(db: Session, user: User, scope: Scope) -> list[DayLine]:
    """The scope's income and spending, by day. Sums to exactly what `flows` reports for it."""
    from_rules = {
        row[0]: bool(row[1])
        for row in db.execute(
            select(RecurringOccurrence.transaction_id, RecurringRule.is_subscription)
            .join(RecurringRule, RecurringRule.id == RecurringOccurrence.rule_id)
            .where(
                RecurringOccurrence.user_id == user.id,
                RecurringOccurrence.transaction_id.is_not(None),
            )
        )
    }
    charges: set[uuid.UUID] = set()
    for interest, fee in db.execute(
        select(
            InstallmentPayment.interest_transaction_id, InstallmentPayment.fee_transaction_id
        ).where(InstallmentPayment.user_id == user.id)
    ):
        charges.update(tid for tid in (interest, fee) if tid)
    financed = set(
        db.scalars(
            select(Transaction.id).where(
                Transaction.user_id == user.id, Transaction.created_via == "installment"
            )
        )
    )

    def classify(transaction_id: uuid.UUID | None, refunds: uuid.UUID | None) -> str:
        # A refund belongs with what it refunds, so it reduces the same part.
        origin = refunds or transaction_id
        if origin in from_rules:
            return "subscription" if from_rules[origin] else "scheduled"
        if origin in charges:
            return "scheduled"
        return "financed" if origin in financed else "variable"

    query = (
        select(
            JournalLine.currency,
            JournalLine.effective_date,
            JournalEntry.category_id,
            LedgerAccount.kind,
            JournalEntry.transaction_id,
            Transaction.refunds_transaction_id,
            func.sum(JournalLine.amount_minor),
        )
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .outerjoin(Transaction, Transaction.id == JournalEntry.transaction_id)
        .where(LedgerAccount.kind.in_(FLOW_TYPES))
        .group_by(
            JournalLine.currency,
            JournalLine.effective_date,
            JournalEntry.category_id,
            LedgerAccount.kind,
            JournalEntry.transaction_id,
            Transaction.refunds_transaction_id,
        )
    )
    out: list[DayLine] = []
    for code, day, category_id, kind, transaction_id, refunds, total in db.execute(
        _entry_filters(query, user, scope)
    ):
        if kind == "expense" and not _ledger_spending(scope):
            continue
        amount = _int(total) * (1 if kind == "expense" else -1)
        if amount:
            out.append(
                DayLine(
                    str(code),
                    _as_date(day),
                    _uuid(category_id),
                    str(kind),
                    classify(_uuid(transaction_id), _uuid(refunds))
                    if kind == "expense"
                    else "income",
                    amount,
                )
            )
    for share in scope_shares(db, user, scope):
        out.append(
            DayLine(
                share.currency,
                share.expense_date,
                share.category_id,
                "expense",
                "trip",
                share.amount,
                share.trip_id,
                share.trip_name,
            )
        )
    return out


# --- Over time


@dataclass(frozen=True)
class Point:
    currency: str
    bucket: date  # the first day of the day, week (Monday), or month
    income: int
    expenses: int


def before_start(value: date) -> date:
    return value - timedelta(days=1)


def month_end_of(value: date) -> date:
    following = (value.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def bucket_start(value: date, grain: str) -> date:
    """Where a date's bucket starts, as PostgreSQL's date_trunc does (weeks start on Monday)."""
    if grain == "month":
        return value.replace(day=1)
    if grain == "week":
        return value - timedelta(days=value.weekday())
    return value


def buckets(start: date, end: date, grain: str) -> list[date]:
    """Every bucket from the one holding `start` to the one holding `end`, empty ones included."""
    out = []
    current = bucket_start(start, grain)
    while current <= end:
        out.append(current)
        if grain == "month":
            current = month_end_of(current) + timedelta(days=1)
        else:
            current += timedelta(days=7 if grain == "week" else 1)
    return out


def series(db: Session, user: User, scope: Scope, grain: str) -> list[Point]:
    """Income and spending per day, week or month, per currency. Empty buckets aren't returned."""
    if grain not in ("day", "week", "month"):
        raise ValueError("grain")
    bucket = func.date_trunc(grain, JournalLine.effective_date).cast(
        JournalLine.effective_date.type
    )
    expense = func.sum(case((LedgerAccount.kind == "expense", JournalLine.amount_minor), else_=0))
    income = func.sum(case((LedgerAccount.kind == "income", -JournalLine.amount_minor), else_=0))
    query = (
        select(JournalLine.currency, bucket, income, expense)
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(LedgerAccount.kind.in_(FLOW_TYPES))
        .group_by(JournalLine.currency, bucket)
        .order_by(bucket)
    )
    found: dict[tuple[str, date], list[int]] = {}
    for code, day, inc, exp in db.execute(_entry_filters(query, user, scope)):
        found[(str(code), _as_date(day))] = [
            _int(inc),
            _int(exp) if _ledger_spending(scope) else 0,
        ]
    for share in scope_shares(db, user, scope):
        key = (share.currency, bucket_start(share.expense_date, grain))
        found.setdefault(key, [0, 0])[1] += share.amount
    return [
        Point(code, day, inc, exp)
        for (code, day), (inc, exp) in sorted(found.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        if inc or exp
    ]


# --- Cash movements and the balance identity (§16.2)


@dataclass
class Movements:
    currency: str
    opening: int = 0
    initial_balances: int = 0
    income: int = 0
    expenses: int = 0
    transfers_in: int = 0
    transfers_out: int = 0
    conversions_in: int = 0
    conversions_out: int = 0
    adjustments: int = 0
    debts_in: int = 0
    debts_out: int = 0
    # Cash paid into trips (expenses paid, settlements paid) and received back (§11.3). Never
    # spending or income: what the person's trips cost them is their share, shown separately.
    trips_in: int = 0
    trips_out: int = 0
    # Money back for purchases (§4.4): it reduces spending, so it's never income.
    refunds: int = 0
    closing: int = 0

    @property
    def reconciles(self) -> bool:
        return self.closing == (
            self.opening + self.initial_balances + self.income - self.expenses
            + self.refunds + self.transfers_in - self.transfers_out + self.conversions_in
            - self.conversions_out + self.adjustments + self.debts_in - self.debts_out
            + self.trips_in - self.trips_out
        )  # fmt: skip


def _asset_lines(user: User, scope: Scope) -> Any:
    query = (
        select(JournalLine)
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .where(JournalLine.user_id == user.id, LedgerAccount.kind == "asset")
    )
    if scope.account_ids:
        query = query.where(LedgerAccount.financial_account_id.in_(scope.account_ids))
    if scope.currencies:
        query = query.where(JournalLine.currency.in_(scope.currencies))
    return query


def movements(db: Session, user: User, scope: Scope) -> dict[str, Movements]:
    """Opening, every kind of movement, and closing, per currency, from asset lines only."""
    out: dict[str, Movements] = {}

    def item(code: str) -> Movements:
        return out.setdefault(code, Movements(code))

    base = _asset_lines(user, scope).subquery()
    line = base.c
    for code, before, through in db.execute(
        select(
            line.currency,
            func.sum(case((line.effective_date < scope.date_from, line.amount_minor), else_=0)),
            func.sum(case((line.effective_date <= scope.date_to, line.amount_minor), else_=0)),
        ).group_by(line.currency)
    ):
        item(str(code)).opening = _int(before)
        item(str(code)).closing = _int(through)

    # Within the period: net per transaction and currency, so a reversed transfer nets to nothing
    # instead of appearing both in and out.
    per_txn = (
        select(
            Transaction.id.label("txn"),
            Transaction.type.label("type"),
            (
                Transaction.counter_currency.is_not(None)
                & (Transaction.counter_currency != Transaction.currency)
            ).label("is_cross"),
            line.currency.label("currency"),
            func.sum(line.amount_minor).label("net"),
        )
        .select_from(base)
        .join(JournalEntry, JournalEntry.id == line.entry_id)
        .join(Transaction, Transaction.id == JournalEntry.transaction_id)
        .where(line.effective_date >= scope.date_from, line.effective_date <= scope.date_to)
        .group_by(Transaction.id, Transaction.type, "is_cross", line.currency)
        .subquery()
    )
    p = per_txn.c
    rows = db.execute(
        select(
            p.currency,
            p.type,
            p.is_cross,
            func.sum(case((p.net > 0, p.net), else_=0)),
            func.sum(case((p.net < 0, -p.net), else_=0)),
        ).group_by(p.currency, p.type, p.is_cross)
    )
    for code, kind, cross, inflow, outflow in rows:
        m = item(str(code))
        inflow, outflow = _int(inflow), _int(outflow)
        if kind == "income":
            m.income += inflow - outflow
        elif kind == "expense":
            m.expenses += outflow - inflow
        elif kind == "opening_balance":
            m.initial_balances += inflow - outflow
        elif kind == "adjustment":
            m.adjustments += inflow - outflow
        elif kind == "transfer":
            if cross:
                m.conversions_in += inflow
                m.conversions_out += outflow
            else:
                m.transfers_in += inflow
                m.transfers_out += outflow
        elif kind in ("debt_start", "debt_payment"):
            m.debts_in += inflow
            m.debts_out += outflow
        elif kind in TRIP_CASH_TYPES:
            m.trips_in += inflow
            m.trips_out += outflow
        elif kind == "refund":
            m.refunds += inflow - outflow
    return out


# --- Recurring and debts


@dataclass(frozen=True)
class OccurrenceLine:
    occurrence: RecurringOccurrence
    rule: RecurringRule
    actual_minor: int | None
    actual_currency: str | None


def occurrences(db: Session, user: User, scope: Scope) -> list[OccurrenceLine]:
    """Expected items whose window overlaps the period, with what was actually posted, if any."""
    query = (
        select(RecurringOccurrence, RecurringRule, Transaction.amount_minor, Transaction.currency)
        .join(RecurringRule, RecurringRule.id == RecurringOccurrence.rule_id)
        .outerjoin(Transaction, Transaction.id == RecurringOccurrence.transaction_id)
        .where(
            RecurringOccurrence.user_id == user.id,
            RecurringOccurrence.window_end >= scope.date_from,
            RecurringOccurrence.window_start <= scope.date_to,
        )
        .order_by(RecurringOccurrence.window_start, RecurringOccurrence.id)
    )
    if scope.currencies:
        query = query.where(RecurringOccurrence.currency.in_(scope.currencies))
    if scope.account_ids:
        query = query.where(RecurringOccurrence.account_id.in_(scope.account_ids))
    return [OccurrenceLine(o, r, amount, code) for o, r, amount, code in db.execute(query).all()]


@dataclass(frozen=True)
class DebtLine:
    debt: Debt
    remaining: int  # at the end of the period
    repaid_in_period: int


def debts(db: Session, user: User, scope: Scope) -> list[DebtLine]:
    """Each debt started by the period's end: what remained then, and what was repaid in it."""
    balances = dict(
        db.execute(
            select(LedgerAccount.debt_id, func.coalesce(func.sum(JournalLine.amount_minor), 0))
            .join(JournalLine, JournalLine.ledger_account_id == LedgerAccount.id)
            .where(
                LedgerAccount.user_id == user.id,
                LedgerAccount.debt_id.is_not(None),
                JournalLine.effective_date <= scope.date_to,
            )
            .group_by(LedgerAccount.debt_id)
        ).all()
    )
    repaid: dict[uuid.UUID, int] = defaultdict(int)
    for debt_id, amount in db.execute(
        select(Transaction.debt_id, func.coalesce(func.sum(Transaction.debt_amount_minor), 0))
        .where(
            Transaction.user_id == user.id,
            Transaction.type == "debt_payment",
            Transaction.status == "posted",
            Transaction.effective_date >= scope.date_from,
            Transaction.effective_date <= scope.date_to,
        )
        .group_by(Transaction.debt_id)
    ):
        if debt_id is not None:
            repaid[debt_id] = _int(amount)
    query = select(Debt).where(Debt.user_id == user.id, Debt.start_date <= scope.date_to)
    if scope.currencies:
        query = query.where(Debt.currency.in_(scope.currencies))
    out = []
    for debt in db.scalars(query.order_by(Debt.created_at)):
        balance = int(balances.get(debt.id, 0))
        remaining = -balance if debt.direction == "i_owe" else balance
        out.append(DebtLine(debt, remaining, repaid.get(debt.id, 0)))
    return out


# --- Budgets (§6)


def spent(
    db: Session,
    user: User,
    date_from: date,
    date_to: date,
    currency: str | None,
    category_id: uuid.UUID | None,
) -> dict[str, int]:
    """Spending per currency in a period, for one category or all. Budgets call only this.

    Always includes the person's trip shares (§11.10, OD-10): an overall budget counts every share
    in its currency; a category budget counts shares whose system category is that category (a
    custom category has no system key, so no share counts there).
    """
    query = (
        select(JournalLine.currency, func.sum(JournalLine.amount_minor))
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalLine.user_id == user.id,
            LedgerAccount.kind == "expense",
            JournalLine.effective_date >= date_from,
            JournalLine.effective_date <= date_to,
        )
        .group_by(JournalLine.currency)
    )
    if currency is not None:
        query = query.where(JournalLine.currency == currency)
    if category_id is not None:
        query = query.where(JournalEntry.category_id == category_id)
    out: dict[str, int] = defaultdict(int)
    for code, total in db.execute(query):
        out[str(code)] += _int(total)
    for share in share_lines(
        db,
        user,
        date_from,
        date_to,
        (currency,) if currency else (),
        (category_id,) if category_id else (),
    ):
        out[share.currency] += share.amount
    return dict(out)


def percent(part: int, whole: int) -> Decimal:
    """part / whole as a percentage with one decimal, half up. Decimal only."""
    if whole <= 0:
        return Decimal(0)
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


@dataclass
class Report:
    scope: Scope
    flows: dict[str, Flows] = field(default_factory=dict)
    previous: dict[str, Flows] = field(default_factory=dict)
    categories: list[CategoryTotal] = field(default_factory=list)
    previous_categories: list[CategoryTotal] = field(default_factory=list)
    movements: dict[str, Movements] = field(default_factory=dict)
    occurrences: list[OccurrenceLine] = field(default_factory=list)
    debts: list[DebtLine] = field(default_factory=list)
    trip_shares: list[ShareLine] = field(default_factory=list)
    count: int = 0


def report(db: Session, user: User, scope: Scope) -> Report:
    """Everything a report shows, from one consistent snapshot (the caller's transaction)."""
    previous = scope.previous()
    return Report(
        scope=scope,
        flows=flows(db, user, scope),
        previous=flows(db, user, previous),
        categories=by_category(db, user, scope),
        previous_categories=by_category(db, user, previous),
        movements=movements(db, user, scope) if scope.balances_applicable else {},
        occurrences=occurrences(db, user, scope),
        debts=debts(db, user, scope),
        trip_shares=scope_shares(db, user, scope),
        count=int(db.scalar(_count_query(user, scope, group_currency=False)) or 0),
    )
