"""Personal debts (FINANCIAL_RULES.md §4.10): borrowing isn't income, lending isn't spending, and a
repayment is neither.

Each debt has its own ledger account: `debt_payable` (I owe) or `debt_receivable` (owed to me), in
the debt's currency. The remaining amount is that account's balance, never a stored number.

| Event                         | Debit (+)         | Credit (-)        |
|-------------------------------|-------------------|-------------------|
| I borrowed, money arrived     | the account       | debt payable      |
| I repay                       | debt payable      | the account       |
| I lent, money left            | debt receivable   | the account       |
| I'm repaid                    | the account       | debt receivable   |
| It existed before (no money)  | opening equity    | debt payable      |
| ... owed to me, before        | debt receivable   | opening equity    |

Across currencies both real amounts are entered and the entry passes through FX clearing in each
currency, as a transfer does (§4.5). Nothing adds interest, fees, or forgiveness: a payment larger
than what remains is refused, and any extra must be recorded separately as income or an expense.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import Debt, FinancialAccount, JournalLine, LedgerAccount, Transaction, User
from app.services import ledger, money
from app.services.accounts import today_for
from app.services.movements import lock_accounts
from app.services.sessions import audit

DEBT_KIND = {"i_owe": "debt_payable", "owed_to_me": "debt_receivable"}


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found")


def owned(db: Session, user: User, debt_id: uuid.UUID, *, lock: bool = False) -> Debt:
    query = select(Debt).where(Debt.id == debt_id, Debt.user_id == user.id)
    if lock:
        query = query.with_for_update()
    found = db.scalar(query)
    if found is None:
        raise not_found()
    return found


def debt_ledger(db: Session, debt: Debt) -> uuid.UUID:
    existing = db.scalar(select(LedgerAccount.id).where(LedgerAccount.debt_id == debt.id))
    if existing is not None:
        return existing
    row = LedgerAccount(
        user_id=debt.user_id,
        kind=DEBT_KIND[debt.direction],
        currency=debt.currency,
        debt_id=debt.id,
    )
    db.add(row)
    db.flush()
    return row.id


def remaining(db: Session, debt: Debt) -> int:
    """What is still owed, in minor units of the debt's currency: from the ledger only."""
    total = db.scalar(
        select(func.coalesce(func.sum(JournalLine.amount_minor), 0))
        .join(LedgerAccount, LedgerAccount.id == JournalLine.ledger_account_id)
        .where(LedgerAccount.debt_id == debt.id)
    )
    balance = int(total or 0)
    return -balance if debt.direction == "i_owe" else balance


def _cash_lines(
    db: Session,
    user: User,
    debt: Debt,
    account: FinancialAccount,
    account_minor: int,
    debt_minor: int,
    money_in: bool,
) -> list[ledger.Line]:
    """Money moving between an account and a debt; `money_in` is True when the account gains."""
    asset = ledger.asset_account(db, account)
    debt_account = debt_ledger(db, debt)
    sign = 1 if money_in else -1
    lines = [
        ledger.Line(asset, account.currency, sign * account_minor),
        ledger.Line(debt_account, debt.currency, -sign * debt_minor),
    ]
    if account.currency != debt.currency:
        lines += [
            ledger.Line(
                ledger.shared_account(db, user.id, "fx_clearing", account.currency),
                account.currency,
                -sign * account_minor,
            ),
            ledger.Line(
                ledger.shared_account(db, user.id, "fx_clearing", debt.currency),
                debt.currency,
                sign * debt_minor,
            ),
        ]
    elif account_minor != debt_minor:
        raise ApiError(422, "amounts_differ", "Within one currency, both amounts are the same")
    return lines


@dataclass(frozen=True)
class DebtDraft:
    direction: str
    counterparty: str
    currency: str
    amount: str
    start_date: date | None
    notes: str | None
    # The account the money moved through when the debt started; None if it existed before.
    account_id: uuid.UUID | None
    # Across currencies: the amount in the account's currency.
    account_amount: str | None


def _positive(db: Session, amount: str, currency: str) -> int:
    minor, _ = money.parse(db, amount, currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    return minor


def _account_amount(
    db: Session, account: FinancialAccount, debt_currency: str, debt_minor: int, text: str | None
) -> int:
    if account.currency == debt_currency:
        if text is not None and _positive(db, text, account.currency) != debt_minor:
            raise ApiError(422, "amounts_differ", "Within one currency, both amounts are the same")
        return debt_minor
    if text is None:
        raise ApiError(
            422,
            "account_amount_required",
            "Enter the amount in the account's currency too",
            {"account_currency": account.currency},
        )
    return _positive(db, text, account.currency)


def _check_date(user: User, value: date | None) -> date:
    today = today_for(user)
    if value is not None and value > today:
        raise ApiError(422, "date_in_future", "The date can't be in the future")
    return value or today


def create(db: Session, user: User, draft: DebtDraft) -> tuple[Debt, Transaction]:
    if draft.direction not in DEBT_KIND:
        raise ApiError(422, "validation_error", "Unknown direction")
    debt_minor = _positive(db, draft.amount, draft.currency)
    when = _check_date(user, draft.start_date)
    debt = Debt(
        user_id=user.id,
        direction=draft.direction,
        counterparty=draft.counterparty,
        currency=draft.currency,
        original_amount_minor=debt_minor,
        start_date=when,
        notes=draft.notes,
    )
    db.add(debt)
    db.flush()

    if draft.account_id is not None:
        account = lock_accounts(db, user, [draft.account_id])[draft.account_id]
        if account.status != "active":
            raise ApiError(409, "account_archived", "An archived account accepts no new entries")
        account_minor = _account_amount(
            db, account, debt.currency, debt_minor, draft.account_amount
        )
        # Borrowing brings money in; lending sends it out.
        lines = _cash_lines(
            db, user, debt, account, account_minor, debt_minor, draft.direction == "i_owe"
        )
        document = _document(user, "debt_start", debt, account, account_minor, debt_minor, when)
    else:
        equity = ledger.shared_account(db, user.id, "equity_opening", debt.currency)
        debt_account = debt_ledger(db, debt)
        sign = -1 if draft.direction == "i_owe" else 1
        lines = [
            ledger.Line(debt_account, debt.currency, sign * debt_minor),
            ledger.Line(equity, debt.currency, -sign * debt_minor),
        ]
        document = _document(user, "debt_start", debt, None, debt_minor, debt_minor, when)
    db.add(document)
    db.flush()
    ledger.post(db, document, lines)
    audit(db, "debt.created", user.id, entity_type="debt", entity_id=debt.id)
    return debt, document


def _document(
    user: User,
    kind: str,
    debt: Debt,
    account: FinancialAccount | None,
    amount_minor: int,
    debt_minor: int,
    when: date,
    *,
    description: str | None = None,
    via: str = "form",
) -> Transaction:
    return Transaction(
        user_id=user.id,
        type=kind,
        account_id=account.id if account else None,
        amount_minor=amount_minor,
        currency=account.currency if account else debt.currency,
        debt_id=debt.id,
        debt_amount_minor=debt_minor,
        occurred_at=datetime.now(UTC),
        effective_date=when,
        description=description,
        created_via=via,
    )


@dataclass(frozen=True)
class Payment:
    account_id: uuid.UUID
    amount: str  # in the debt's currency
    account_amount: str | None  # in the account's currency, when it differs
    effective_date: date | None
    description: str | None


def pay(
    db: Session, user: User, debt_id: uuid.UUID, payment: Payment, *, via: str = "form"
) -> Transaction:
    """A repayment (or being repaid). Never income or spending; never more than remains."""
    debt = owned(db, user, debt_id, lock=True)  # serializes payments on one debt
    if debt.status != "active":
        raise ApiError(409, "debt_archived", "This debt is archived")
    account = lock_accounts(db, user, [payment.account_id])[payment.account_id]
    if account.status != "active":
        raise ApiError(409, "account_archived", "An archived account accepts no new entries")
    debt_minor = _positive(db, payment.amount, debt.currency)
    left = remaining(db, debt)
    if left <= 0:
        raise ApiError(409, "debt_settled", "Nothing remains on this debt")
    if debt_minor > left:
        exponent = money.currency(db, debt.currency, for_new_records=False).exponent
        raise ApiError(
            422,
            "exceeds_remaining",
            "That's more than remains; record any extra separately",
            {"remaining": money.to_amount(left, exponent), "currency": debt.currency},
        )
    account_minor = _account_amount(db, account, debt.currency, debt_minor, payment.account_amount)
    when = _check_date(user, payment.effective_date)
    # I repay: money leaves. I'm repaid: money arrives.
    money_in = debt.direction == "owed_to_me"
    lines = _cash_lines(db, user, debt, account, account_minor, debt_minor, money_in)
    document = _document(
        user, "debt_payment", debt, account, account_minor, debt_minor, when,
        description=payment.description, via=via,
    )  # fmt: skip
    db.add(document)
    db.flush()
    ledger.post(db, document, lines)
    audit(db, "debt.payment", user.id, entity_type="debt", entity_id=debt.id)
    return document


def has_live_payments(db: Session, debt_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.debt_id == debt_id,
                Transaction.type == "debt_payment",
                Transaction.status == "posted",
            )
        )
        or 0
    ) > 0


def archive(db: Session, user: User, debt_id: uuid.UUID, version: int, archived: bool) -> Debt:
    debt = owned(db, user, debt_id, lock=True)
    if debt.version != version:
        raise ApiError(
            412, "version_conflict", "The debt changed; reload it", {"version": debt.version}
        )
    if archived and remaining(db, debt) != 0:
        raise ApiError(409, "debt_not_settled", "Only a settled debt can be archived")
    debt.status = "archived" if archived else "active"
    debt.version += 1
    db.flush()
    return debt
