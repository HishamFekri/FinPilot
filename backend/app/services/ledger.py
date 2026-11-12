"""Posting to the personal ledger, and deriving balances from it (FINANCIAL_RULES.md §4).

Users never see debits and credits. Every financial effect is one journal entry whose lines sum to
zero per currency. This module checks that before writing, and the database checks it again at
commit, so a bug here produces an error instead of wrong money.

Balances are always derived: the sum of an account's asset lines up to a date (§4.7). There is no
stored balance to drift.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import FinancialAccount, JournalEntry, JournalLine, LedgerAccount, Transaction

# Ledger accounts that aren't tied to a financial account: one per user, kind, and currency.
SHARED_KINDS = frozenset(
    {"income", "expense", "equity_opening", "equity_adjustment", "fx_clearing", "trip_clearing"}
)


@dataclass(frozen=True)
class Line:
    ledger_account_id: uuid.UUID
    currency: str
    amount_minor: int  # debit positive, credit negative


class UnbalancedEntryError(ValueError):
    """A programming error: an entry that would break L1 or L2. Never caused by user input."""


def check_balanced(lines: Sequence[Line]) -> None:
    if len(lines) < 2:
        raise UnbalancedEntryError("a journal entry needs at least two lines")
    totals: dict[str, int] = defaultdict(int)
    for line in lines:
        if line.amount_minor == 0:
            raise UnbalancedEntryError("a journal line can't be zero")
        totals[line.currency] += line.amount_minor
    if any(total != 0 for total in totals.values()):
        raise UnbalancedEntryError("journal lines must sum to zero in each currency")


def asset_account(db: Session, account: FinancialAccount) -> uuid.UUID:
    """The asset ledger account behind a financial account, created with it."""
    existing = db.scalar(
        select(LedgerAccount.id).where(LedgerAccount.financial_account_id == account.id)
    )
    if existing is not None:
        return existing
    ledger = LedgerAccount(
        user_id=account.user_id,
        kind="asset",
        currency=account.currency,
        financial_account_id=account.id,
    )
    db.add(ledger)
    db.flush()
    return ledger.id


def shared_account(db: Session, user_id: uuid.UUID, kind: str, currency: str) -> uuid.UUID:
    """Created once, even under concurrency (§14: insert, ignore conflict, select)."""
    if kind not in SHARED_KINDS:
        raise ValueError(f"{kind} ledger accounts belong to a financial account")
    db.execute(
        insert(LedgerAccount)
        .values(user_id=user_id, kind=kind, currency=currency, financial_account_id=None)
        .on_conflict_do_nothing(
            index_elements=["user_id", "kind", "currency"],
            index_where=and_(
                LedgerAccount.financial_account_id.is_(None), LedgerAccount.debt_id.is_(None)
            ),
        )
    )
    found = db.scalar(
        select(LedgerAccount.id).where(
            LedgerAccount.user_id == user_id,
            LedgerAccount.kind == kind,
            LedgerAccount.currency == currency,
            LedgerAccount.financial_account_id.is_(None),
        )
    )
    if found is None:  # pragma: no cover - the insert above guarantees a row
        raise RuntimeError("ledger account was not created")
    return found


def post(
    db: Session,
    transaction: Transaction,
    lines: Sequence[Line],
    *,
    category_id: uuid.UUID | None = None,
    effective_date: date | None = None,
    reverses: JournalEntry | None = None,
) -> JournalEntry:
    """Writes one immutable journal entry for a transaction, dated as the transaction unless a date
    is given. `category_id` is the entry's category dimension (FINANCIAL_RULES.md 4.2)."""
    check_balanced(lines)
    when = effective_date or transaction.effective_date
    entry = JournalEntry(
        user_id=transaction.user_id,
        transaction_id=transaction.id,
        transaction_type=transaction.type,
        effective_date=when,
        category_id=category_id,
        reverses_entry_id=reverses.id if reverses else None,
    )
    db.add(entry)
    db.flush()
    db.add_all(
        JournalLine(
            entry_id=entry.id,
            user_id=transaction.user_id,
            effective_date=when,
            ledger_account_id=line.ledger_account_id,
            currency=line.currency,
            amount_minor=line.amount_minor,
        )
        for line in lines
    )
    db.flush()
    return entry


def live_entry(db: Session, transaction: Transaction) -> JournalEntry | None:
    """The transaction's current effect: its one entry that is neither a reversal nor reversed
    (INV-05 guarantees at most one)."""
    reversed_ids = select(JournalEntry.reverses_entry_id).where(
        JournalEntry.transaction_id == transaction.id, JournalEntry.reverses_entry_id.is_not(None)
    )
    return db.scalar(
        select(JournalEntry)
        .where(
            JournalEntry.transaction_id == transaction.id,
            JournalEntry.reverses_entry_id.is_(None),
            JournalEntry.id.not_in(reversed_ids),
        )
        .order_by(JournalEntry.posted_at.desc())
        .limit(1)
    )


def reverse(db: Session, transaction: Transaction, entry: JournalEntry) -> JournalEntry:
    """Cancels an entry by posting its exact negation, on the same date and in the same category,
    so every period and category total nets to what it would have been without it (4.8)."""
    lines = [
        Line(line.ledger_account_id, line.currency, -line.amount_minor)
        for line in db.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id))
    ]
    return post(
        db,
        transaction,
        lines,
        category_id=entry.category_id,
        effective_date=entry.effective_date,
        reverses=entry,
    )


def balances(
    db: Session, user_id: uuid.UUID, account_ids: Iterable[uuid.UUID], *, on: date | None = None
) -> dict[uuid.UUID, int]:
    """Each account's balance in minor units: the sum of its asset lines (up to `on`, if given).

    Scoped to the user, so another user's account simply has no balance here.
    """
    ids = list(account_ids)
    if not ids:
        return {}
    join = (JournalLine.ledger_account_id == LedgerAccount.id) & (
        JournalLine.user_id == LedgerAccount.user_id
    )
    if on is not None:
        join &= JournalLine.effective_date <= on
    rows = db.execute(
        select(
            LedgerAccount.financial_account_id, func.coalesce(func.sum(JournalLine.amount_minor), 0)
        )
        .select_from(LedgerAccount)
        .outerjoin(JournalLine, join)
        .where(LedgerAccount.user_id == user_id, LedgerAccount.financial_account_id.in_(ids))
        .group_by(LedgerAccount.financial_account_id)
    )
    found = {account_id: int(total) for account_id, total in rows if account_id is not None}
    return {account_id: found.get(account_id, 0) for account_id in ids}


def has_entries(db: Session, account: FinancialAccount) -> bool:
    """Whether anything was ever recorded on the account, including since-reversed entries."""
    return (
        db.scalar(select(Transaction.id).where(Transaction.account_id == account.id).limit(1))
        is not None
    )
