"""Moving recorded money: balance adjustments and transfers between a user's own accounts.

FINANCIAL_RULES.md §4.4 and §4.5. Every movement is one `transactions` document plus one balanced,
immutable journal entry, written in a single database transaction.

Concurrency. Balances are derived, so posting needs no balance lock (§14). Two things do read a
balance and act on it, and are serialized through row locks on the accounts involved:

- An adjustment reads the current balance to compute the difference. It holds `FOR UPDATE` on its
  account, and refuses if the balance isn't the one the person saw (409 `balance_changed`).
- Archiving reads the balance (it must be zero) and also holds `FOR UPDATE`.

Every other write takes `FOR SHARE` on its accounts, always in ascending ID order, so it waits for
an adjustment or an archive in progress and can't deadlock with another write.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import Debt, FinancialAccount, JournalLine, LedgerAccount, Transaction, User
from app.services import ledger, money
from app.services.accounts import AccountView, not_found, today_for
from app.services.sessions import audit

RATE_DECIMALS = 6


def lock_accounts(
    db: Session, user: User, account_ids: Sequence[uuid.UUID], *, exclusive: bool = False
) -> dict[uuid.UUID, FinancialAccount]:
    """Locks the user's accounts in ascending ID order. Any ID that isn't theirs is a 404."""
    wanted = sorted(set(account_ids))
    query = (
        select(FinancialAccount)
        .where(FinancialAccount.user_id == user.id, FinancialAccount.id.in_(wanted))
        .order_by(FinancialAccount.id)
    )
    query = query.with_for_update() if exclusive else query.with_for_update(read=True)
    found = {account.id: account for account in db.scalars(query)}
    if len(found) != len(wanted):
        raise not_found()
    return found


def _require_active(account: FinancialAccount) -> None:
    if account.status != "active":
        raise ApiError(409, "account_archived", "An archived account accepts no new entries")


def _effective_date(user: User, requested: date | None) -> date:
    today = today_for(user)
    if requested is not None and requested > today:
        raise ApiError(422, "date_in_future", "The date can't be in the future")
    return requested or today


def _now() -> datetime:
    return datetime.now(UTC)


# --- Adjustments


@dataclass(frozen=True)
class AdjustmentPreview:
    account: FinancialAccount
    current_minor: int
    target_minor: int

    @property
    def difference_minor(self) -> int:
        return self.target_minor - self.current_minor


def preview_adjustment(
    db: Session, user: User, account_id: uuid.UUID, target: str, currency: str
) -> AdjustmentPreview:
    """What an adjustment to `target` would post, without posting it (no locks, no writes)."""
    account = db.scalar(
        select(FinancialAccount).where(
            FinancialAccount.id == account_id, FinancialAccount.user_id == user.id
        )
    )
    if account is None:
        raise not_found()
    return _adjustment(db, user, account, target, currency)


def _adjustment(
    db: Session, user: User, account: FinancialAccount, target: str, currency: str
) -> AdjustmentPreview:
    if currency != account.currency:
        raise ApiError(422, "currency_mismatch", "The balance must be in the account's currency")
    target_minor, _ = money.parse(db, target, currency)
    current = ledger.balances(db, user.id, [account.id])[account.id]
    return AdjustmentPreview(account, current, target_minor)


def adjust(
    db: Session,
    user: User,
    account_id: uuid.UUID,
    *,
    target: str,
    expected: str,
    currency: str,
    reason: str | None,
) -> tuple[Transaction, AccountView]:
    """Posts the difference between the recorded balance and the real one, against
    Equity:Adjustment. The person's figure is the target; the backend works out the difference.

    `expected` is the balance the person saw when they typed the target. If anything was recorded
    since, the difference they agreed to is no longer the one that would be posted: 409.
    """
    account = lock_accounts(db, user, [account_id], exclusive=True)[account_id]
    _require_active(account)
    plan = _adjustment(db, user, account, target, currency)
    expected_minor, _ = money.parse(db, expected, currency)
    if expected_minor != plan.current_minor:
        raise ApiError(
            409,
            "balance_changed",
            "The balance changed since it was shown; review the adjustment again",
        )
    difference = plan.difference_minor
    if difference == 0:
        raise ApiError(422, "no_change", "The balance already matches")

    today = today_for(user)
    adjustment = Transaction(
        user_id=user.id,
        type="adjustment",
        account_id=account.id,
        amount_minor=abs(difference),
        direction="increase" if difference > 0 else "decrease",
        currency=account.currency,
        occurred_at=_now(),
        effective_date=today,
        notes=reason,
        created_via="form",
    )
    db.add(adjustment)
    db.flush()
    asset = ledger.asset_account(db, account)
    equity = ledger.shared_account(db, user.id, "equity_adjustment", account.currency)
    ledger.post(
        db,
        adjustment,
        [
            ledger.Line(asset, account.currency, difference),
            ledger.Line(equity, account.currency, -difference),
        ],
    )
    audit(
        db,
        "account.adjusted",
        user.id,
        entity_type="transaction",
        entity_id=adjustment.id,
        account_id=str(account.id),
    )
    return adjustment, AccountView(account, plan.target_minor)


# --- Transfers


@dataclass(frozen=True)
class TransferPlan:
    source: FinancialAccount
    destination: FinancialAccount
    sent_minor: int
    received_minor: int
    sent_exponent: int
    received_exponent: int

    @property
    def cross_currency(self) -> bool:
        return self.source.currency != self.destination.currency

    def implied_rate(self) -> str | None:
        """1 source unit = this many destination units, from the two real amounts (§4.5).

        A display memo only. It never enters a calculation, and nothing is ever converted with it.
        """
        if not self.cross_currency:
            return None
        sent = Decimal(self.sent_minor).scaleb(-self.sent_exponent)
        received = Decimal(self.received_minor).scaleb(-self.received_exponent)
        with localcontext() as context:
            context.prec = 40
            rate = (received / sent).quantize(
                Decimal(1).scaleb(-RATE_DECIMALS), rounding=ROUND_HALF_EVEN
            )
        return format(rate, "f")


def _plan_transfer(
    db: Session,
    source: FinancialAccount,
    destination: FinancialAccount,
    amount: str,
    currency: str,
    received_amount: str | None,
    received_currency: str | None,
) -> TransferPlan:
    if source.id == destination.id:
        raise ApiError(422, "same_account", "Choose two different accounts")
    if currency != source.currency:
        raise ApiError(
            422, "currency_mismatch", "The amount sent is in the source account's currency"
        )
    sent, sent_info = money.parse(db, amount, currency)
    if sent <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")

    if source.currency == destination.currency:
        # One currency: the same amount leaves and arrives. A second figure must agree, if sent.
        if received_amount is not None:
            received, _ = money.parse(db, received_amount, received_currency or currency)
            if received_currency not in (None, currency) or received != sent:
                raise ApiError(
                    422,
                    "amounts_differ",
                    "Within one currency, the amount received equals the amount sent",
                )
        return TransferPlan(source, destination, sent, sent, sent_info.exponent, sent_info.exponent)

    # Two currencies: both real amounts are required. FinPilot never invents either one.
    if received_amount is None:
        raise ApiError(
            422,
            "received_amount_required",
            "Enter the amount that arrived, in the destination account's currency",
        )
    if (received_currency or destination.currency) != destination.currency:
        raise ApiError(
            422, "currency_mismatch", "The amount received is in the destination's currency"
        )
    received, received_info = money.parse(db, received_amount, destination.currency)
    if received <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    return TransferPlan(
        source, destination, sent, received, sent_info.exponent, received_info.exponent
    )


def preview_transfer(
    db: Session,
    user: User,
    source_id: uuid.UUID,
    destination_id: uuid.UUID,
    amount: str,
    currency: str,
    received_amount: str | None,
    received_currency: str | None,
) -> TransferPlan:
    accounts = {
        a.id: a
        for a in db.scalars(
            select(FinancialAccount).where(
                FinancialAccount.user_id == user.id,
                FinancialAccount.id.in_([source_id, destination_id]),
            )
        )
    }
    if source_id not in accounts or destination_id not in accounts:
        raise not_found()
    return _plan_transfer(
        db,
        accounts[source_id],
        accounts[destination_id],
        amount,
        currency,
        received_amount,
        received_currency,
    )


def transfer(
    db: Session,
    user: User,
    *,
    source_id: uuid.UUID,
    destination_id: uuid.UUID,
    amount: str,
    currency: str,
    received_amount: str | None,
    received_currency: str | None,
    effective_date: date | None,
    description: str | None,
) -> tuple[Transaction, TransferPlan]:
    """Moves recorded money between two of the user's accounts: neither income nor spending.

    Same currency: the destination's asset is debited and the source's credited, by one amount.
    Cross-currency: each side posts its own real amount, balanced per currency through FX
    clearing (§4.5), so each currency sums to zero separately.
    """
    if source_id == destination_id:
        raise ApiError(422, "same_account", "Choose two different accounts")
    locked = lock_accounts(db, user, [source_id, destination_id])
    source, destination = locked[source_id], locked[destination_id]
    _require_active(source)
    _require_active(destination)
    plan = _plan_transfer(
        db, source, destination, amount, currency, received_amount, received_currency
    )
    when = _effective_date(user, effective_date)

    document = Transaction(
        user_id=user.id,
        type="transfer",
        account_id=source.id,
        amount_minor=plan.sent_minor,
        currency=source.currency,
        counter_account_id=destination.id,
        counter_amount_minor=plan.received_minor,
        counter_currency=destination.currency,
        occurred_at=_now(),
        effective_date=when,
        description=description,
        created_via="form",
    )
    db.add(document)
    db.flush()

    from_asset = ledger.asset_account(db, source)
    to_asset = ledger.asset_account(db, destination)
    if not plan.cross_currency:
        lines = [
            ledger.Line(to_asset, destination.currency, plan.received_minor),
            ledger.Line(from_asset, source.currency, -plan.sent_minor),
        ]
    else:
        fx_to = ledger.shared_account(db, user.id, "fx_clearing", destination.currency)
        fx_from = ledger.shared_account(db, user.id, "fx_clearing", source.currency)
        lines = [
            ledger.Line(to_asset, destination.currency, plan.received_minor),
            ledger.Line(fx_to, destination.currency, -plan.received_minor),
            ledger.Line(fx_from, source.currency, plan.sent_minor),
            ledger.Line(from_asset, source.currency, -plan.sent_minor),
        ]
    ledger.post(db, document, lines)
    audit(db, "transfer.created", user.id, entity_type="transaction", entity_id=document.id)
    return document, plan


# --- Reading


@dataclass(frozen=True)
class Activity:
    transaction: Transaction
    amount_minor: int  # signed, from this account's side
    other_account_id: uuid.UUID | None


def activity(
    db: Session, user: User, account_id: uuid.UUID, *, limit: int
) -> tuple[FinancialAccount, list[Activity], bool]:
    """What happened to one account, newest first, each amount signed from its point of view."""
    account = db.scalar(
        select(FinancialAccount).where(
            FinancialAccount.id == account_id, FinancialAccount.user_id == user.id
        )
    )
    if account is None:
        raise not_found()
    rows = list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.user_id == user.id,
                Transaction.status == "posted",
                or_(
                    Transaction.account_id == account.id,
                    Transaction.counter_account_id == account.id,
                ),
            )
            .order_by(
                Transaction.effective_date.desc(),
                Transaction.occurred_at.desc(),
                Transaction.id.desc(),
            )
            .limit(limit + 1)
        )
    )
    debt_ids = {row.debt_id for row in rows if row.debt_id}
    owe = (
        {
            d.id: d.direction == "i_owe"
            for d in db.scalars(select(Debt).where(Debt.id.in_(debt_ids)))
        }
        if debt_ids
        else {}
    )
    items: list[Activity] = []
    for row in rows[:limit]:
        if row.debt_id is not None:
            # Borrowing and being repaid bring money in; lending and repaying send it out.
            incoming = (row.type == "debt_start") == owe.get(row.debt_id, True)
            amount, other = (row.amount_minor if incoming else -row.amount_minor), None
        elif row.type == "transfer":
            incoming = row.counter_account_id == account.id
            amount = (row.counter_amount_minor or 0) if incoming else -row.amount_minor
            other = row.account_id if incoming else row.counter_account_id
        elif row.type in ("income", "trip_settlement_received", "refund"):
            amount, other = row.amount_minor, None
        elif row.type in ("expense", "trip_expense_payment", "trip_settlement_paid"):
            amount, other = -row.amount_minor, None
        else:
            amount = row.amount_minor if row.direction == "increase" else -row.amount_minor
            other = None
        items.append(Activity(row, amount, other))
    return account, items, len(rows) > limit


@dataclass(frozen=True)
class CurrencyTotal:
    currency: str
    amount_minor: int
    accounts: int


def totals(db: Session, user: User) -> list[CurrencyTotal]:
    """Active accounts' balances added up per currency, never across currencies (§12, R9)."""
    per_account = (
        select(
            FinancialAccount.id.label("account_id"),
            FinancialAccount.currency.label("currency"),
            func.coalesce(func.sum(JournalLine.amount_minor), 0).label("balance"),
        )
        .select_from(FinancialAccount)
        .join(
            LedgerAccount,
            (LedgerAccount.financial_account_id == FinancialAccount.id)
            & (LedgerAccount.user_id == FinancialAccount.user_id),
        )
        .outerjoin(
            JournalLine,
            (JournalLine.ledger_account_id == LedgerAccount.id)
            & (JournalLine.user_id == LedgerAccount.user_id),
        )
        .where(FinancialAccount.user_id == user.id, FinancialAccount.status == "active")
        .group_by(FinancialAccount.id, FinancialAccount.currency)
        .subquery()
    )
    rows = db.execute(
        select(
            per_account.c.currency,
            func.sum(per_account.c.balance),
            func.count(),
        )
        .group_by(per_account.c.currency)
        .order_by(func.count().desc(), per_account.c.currency)
    )
    # SUM(bigint) is numeric in PostgreSQL: exact, and returned as Decimal.
    return [CurrencyTotal(str(c), int(Decimal(str(total))), int(str(n))) for c, total, n in rows]
