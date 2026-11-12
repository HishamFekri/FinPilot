"""Monthly budgets (FINANCIAL_RULES.md §6).

A budget is a series `(category or overall, currency)` of effective-dated rows; the limit for a
month is the row with the latest `effective_from_month` on or before it. A row without an amount
ends the budget from that month. Changing a limit writes a row from the chosen month, so earlier
months keep their figures; editing the same month again replaces that month's row.

**Spent** comes only from `reporting.spent`: expense ledger lines dated in the month, in the
budget's currency, in its category (or all categories for an overall budget). So income,
transfers, opening balances, adjustments and debts never count, a correction counts in its
corrected month, and a void nets to zero. Spending in other currencies is never converted (OD-7):
it's listed as not counted.
Trip shares are part of `spent` (OD-10, §11.10): the person's share of each approved trip expense,
on its expense date, in the trip's base currency, in the category with the same system key (an
overall budget counts all of them). Cash paid into a trip is never spending.

States: **exceeded** when spent > limit; **approaching** at `alert_percent` (80 by default) or more;
otherwise **normal**. Percentages use Decimal, rounded half up to one decimal.
"""

import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import Budget, Category, Transaction, User
from app.services import money, reporting
from app.services.categories import owned as owned_category
from app.services.sessions import audit


def month_start(value: date) -> date:
    return value.replace(day=1)


def month_end(value: date) -> date:
    return value.replace(day=monthrange(value.year, value.month)[1])


def add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True)
class Key:
    category_id: uuid.UUID | None
    currency: str


@dataclass(frozen=True)
class BudgetMonth:
    key: Key
    row: Budget  # the row in effect
    month: date
    limit: int
    spent: int
    other_currencies: dict[str, int]  # same category, not counted (OD-7)

    @property
    def remaining(self) -> int:
        return self.limit - self.spent

    @property
    def percent(self) -> Decimal:
        return reporting.percent(self.spent, self.limit)

    @property
    def state(self) -> str:
        if self.spent > self.limit:
            return "exceeded"
        if self.percent >= self.row.alert_percent:
            return "approaching"
        return "normal"


def _rows(db: Session, user: User) -> list[Budget]:
    return list(
        db.scalars(
            select(Budget)
            .where(Budget.user_id == user.id)
            .order_by(Budget.effective_from_month, Budget.created_at)
        )
    )


def _in_effect(rows: list[Budget], month: date) -> dict[Key, Budget]:
    """For each series, the row in effect for a month (the latest one starting on or before it)."""
    current: dict[Key, Budget] = {}
    for row in rows:
        if row.effective_from_month <= month:
            current[Key(row.category_id, row.currency)] = row
    return current


def month(db: Session, user: User, when: date) -> list[BudgetMonth]:
    """Every budget active in a month, with its spending. Ended budgets aren't included."""
    first, last = month_start(when), month_end(when)
    out = []
    for key, row in _in_effect(_rows(db, user), first).items():
        if row.amount_minor is None:
            continue
        spent = reporting.spent(db, user, first, last, None, key.category_id)
        out.append(
            BudgetMonth(
                key=key,
                row=row,
                month=first,
                limit=row.amount_minor,
                spent=spent.get(key.currency, 0),
                other_currencies={c: v for c, v in spent.items() if c != key.currency and v},
            )
        )
    # Overall budgets first, then by how much of the limit is used.
    return sorted(out, key=lambda b: (b.key.category_id is not None, -b.percent, b.key.currency))


def archived(db: Session, user: User, when: date) -> list[tuple[Key, Budget, Budget | None]]:
    """Series ended by this month: the ending row and the last limit before it."""
    rows = _rows(db, user)
    out = []
    for key, row in _in_effect(rows, month_start(when)).items():
        if row.amount_minor is not None:
            continue
        before = [
            r
            for r in rows
            if Key(r.category_id, r.currency) == key
            and r.effective_from_month < row.effective_from_month
            and r.amount_minor is not None
        ]
        out.append((key, row, before[-1] if before else None))
    return out


def _check_category(db: Session, user: User, category_id: uuid.UUID | None) -> Category | None:
    if category_id is None:
        return None
    category = owned_category(db, user, category_id)
    if category.kind != "expense":
        raise ApiError(422, "category_kind_mismatch", "Budgets are for expense categories")
    return category


def _upsert(
    db: Session,
    user: User,
    key: Key,
    from_month: date,
    amount_minor: int | None,
    alert_percent: int,
) -> Budget:
    values = {
        "user_id": user.id,
        "category_id": key.category_id,
        "currency": key.currency,
        "amount_minor": amount_minor,
        "effective_from_month": from_month,
        "alert_percent": alert_percent,
    }
    statement = insert(Budget).values(**values)
    if key.category_id is None:
        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "currency", "effective_from_month"],
            index_where=Budget.category_id.is_(None),
            set_={"amount_minor": amount_minor, "alert_percent": alert_percent},
        )
    else:
        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "category_id", "currency", "effective_from_month"],
            index_where=Budget.category_id.is_not(None),
            set_={"amount_minor": amount_minor, "alert_percent": alert_percent},
        )
    db.execute(statement)
    db.flush()
    query = select(Budget).where(
        Budget.user_id == user.id,
        Budget.currency == key.currency,
        Budget.effective_from_month == from_month,
    )
    query = query.where(
        Budget.category_id.is_(None)
        if key.category_id is None
        else Budget.category_id == key.category_id
    )
    row = db.scalar(query)
    if row is None:  # pragma: no cover - the upsert just wrote it
        raise ApiError(500, "internal_error", "Budget not saved")
    db.refresh(row)
    return row


def set_limit(
    db: Session,
    user: User,
    *,
    category_id: uuid.UUID | None,
    amount: str,
    currency: str,
    from_month: date,
    alert_percent: int,
) -> Budget:
    """Creates a budget, or changes its limit from a month on. Earlier months keep theirs."""
    _check_category(db, user, category_id)
    minor, _ = money.parse(db, amount, currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The limit must be greater than zero")
    if not 1 <= alert_percent <= 100:
        raise ApiError(422, "validation_error", "The warning level is between 1 and 100 percent")
    row = _upsert(
        db, user, Key(category_id, currency), month_start(from_month), minor, alert_percent
    )
    audit(db, "budget.set", user.id, entity_type="budget", entity_id=row.id)
    return row


def end(
    db: Session, user: User, *, category_id: uuid.UUID | None, currency: str, from_month: date
) -> Budget:
    """Archives a budget from a month on. Earlier months still show it."""
    key = Key(category_id, currency)
    first = month_start(from_month)
    current = _in_effect(_rows(db, user), first).get(key)
    if current is None or current.amount_minor is None:
        raise ApiError(404, "not_found", "No budget is in effect then")
    row = _upsert(db, user, key, first, None, current.alert_percent)
    audit(db, "budget.ended", user.id, entity_type="budget", entity_id=row.id)
    return row


def restore(
    db: Session, user: User, *, category_id: uuid.UUID | None, currency: str, from_month: date
) -> Budget:
    """Brings an ended budget back from a month on, with the limit it had before it ended."""
    key = Key(category_id, currency)
    first = month_start(from_month)
    ended = next((e for e in archived(db, user, first) if e[0] == key), None)
    if ended is None:
        raise ApiError(404, "not_found", "No ended budget to restore then")
    _, row, last = ended
    if last is None or last.amount_minor is None:
        raise ApiError(409, "no_previous_limit", "Set a new limit instead")
    restored = _upsert(db, user, key, first, last.amount_minor, row.alert_percent)
    audit(db, "budget.restored", user.id, entity_type="budget", entity_id=restored.id)
    return restored


def delete(
    db: Session, user: User, *, category_id: uuid.UUID | None, currency: str, current: date
) -> int:
    """Removes a budget entirely, only when no month before the current one uses it: a budget never
    holds money, but past months' reports keep showing the limits they had. Otherwise end it."""
    key = Key(category_id, currency)
    rows = [r for r in _rows(db, user) if Key(r.category_id, r.currency) == key]
    if not rows:
        raise ApiError(404, "not_found", "No such budget")
    if any(r.effective_from_month < month_start(current) for r in rows):
        raise ApiError(
            409,
            "budget_has_history",
            "Earlier months use this budget, so it can only be ended from a month on",
        )
    for r in rows:
        db.delete(r)
    db.flush()
    audit(db, "budget.deleted", user.id, entity_type="budget", entity_id=rows[0].id)
    return len(rows)


def history(db: Session, user: User, key: Key) -> list[Budget]:
    return [r for r in _rows(db, user) if Key(r.category_id, r.currency) == key]


def recent_months(
    db: Session, user: User, key: Key, until: date, count: int = 6
) -> list[tuple[date, int | None, int]]:
    """The last few months: the limit in effect (None when there was none) and what was spent."""
    rows = history(db, user, key)
    out = []
    for back in range(count - 1, -1, -1):
        first = add_months(month_start(until), -back)
        effective = [r for r in rows if r.effective_from_month <= first]
        limit = effective[-1].amount_minor if effective else None
        spent = reporting.spent(db, user, first, month_end(first), key.currency, key.category_id)
        out.append((first, limit, spent.get(key.currency, 0)))
    return out


def contributing(
    db: Session, user: User, key: Key, when: date, *, excluded: bool = False
) -> list[Transaction]:
    """The expenses (and refunds, which reduce them) behind a month's figure: posted, in the
    month, currency and category.

    `Transaction.currency` is the posted (account) currency, so an expense entered as EUR 22 on a
    TRY account is here once, as its posted TRY amount; the EUR is only a memo. With `excluded`,
    the same category's expenses posted in other currencies: listed, never counted (OD-7).
    """
    query = select(Transaction).where(
        Transaction.user_id == user.id,
        Transaction.type.in_(("expense", "refund")),
        Transaction.status == "posted",
        Transaction.currency != key.currency if excluded else Transaction.currency == key.currency,
        Transaction.effective_date >= month_start(when),
        Transaction.effective_date <= month_end(when),
    )
    if key.category_id is not None:
        query = query.where(Transaction.category_id == key.category_id)
    return list(
        db.scalars(
            query.order_by(Transaction.effective_date.desc(), Transaction.occurred_at.desc())
        )
    )


def contributing_shares(db: Session, user: User, key: Key, when: date) -> list[reporting.ShareLine]:
    """The trip shares behind a month's figure, newest first (they count in `spent`)."""
    lines = reporting.share_lines(
        db, user, month_start(when), month_end(when), (key.currency,),
        (key.category_id,) if key.category_id else (),
    )  # fmt: skip
    return sorted(lines, key=lambda s: (s.expense_date, str(s.version_id)), reverse=True)
