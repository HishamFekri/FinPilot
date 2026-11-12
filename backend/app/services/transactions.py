"""Income and expense, history, corrections, and voids (FINANCIAL_RULES.md §4.4, §4.8).

Posting:

| Type    | Debit (+)                | Credit (-)               | Entry category |
|---------|--------------------------|--------------------------|----------------|
| expense | Expense (user, currency) | the account's asset      | the expense's  |
| income  | the account's asset      | Income (user, currency)  | the income's   |

A correction never edits a journal line. In one database transaction it reverses the live entry
(same date and category as the original, so past periods net out) and posts a replacement, then
updates the user-facing document and its version. A void is the reversal alone. Only description
and note changes skip the ledger, because they carry no money.
"""

import base64
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    Category,
    FinancialAccount,
    JournalEntry,
    RecurringOccurrence,
    Transaction,
    User,
)
from app.services import conversion, debts, fx, ledger, money
from app.services.accounts import today_for
from app.services.categories import owned as owned_category
from app.services.movements import lock_accounts
from app.services.sessions import audit

MONEY_FIELDS = frozenset({"amount", "account_id", "category_id", "effective_date", "conversion"})


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found")


@dataclass(frozen=True)
class Draft:
    """An income or expense as the person entered it, before checks."""

    type: str
    account_id: uuid.UUID
    amount: str
    currency: str
    category_id: uuid.UUID
    effective_date: date | None
    description: str | None
    notes: str | None
    # Needed when `currency` isn't the account's: how the amount becomes the account's (§4.6).
    conversion: conversion.Conversion | None = None


@dataclass
class Recorded:
    transaction: Transaction
    warnings: list[str]


def _check_date(user: User, value: date | None) -> date:
    today = today_for(user)
    if value is not None and value > today:
        raise ApiError(422, "date_in_future", "The date can't be in the future")
    return value or today


def _check_category(category: Category, kind: str, *, allow_archived: bool = False) -> None:
    if category.kind != kind:
        raise ApiError(422, "category_kind_mismatch", "Choose a category of the same kind")
    if category.status != "active" and not allow_archived:
        raise ApiError(409, "category_archived", "An archived category can't be used")


def _check_active(account: FinancialAccount) -> None:
    if account.status != "active":
        raise ApiError(409, "account_archived", "An archived account accepts no new entries")


def _lines(
    db: Session, user: User, kind: str, account: FinancialAccount, amount_minor: int
) -> list[ledger.Line]:
    asset = ledger.asset_account(db, account)
    counter = ledger.shared_account(db, user.id, kind, account.currency)
    if kind == "expense":
        return [
            ledger.Line(counter, account.currency, amount_minor),
            ledger.Line(asset, account.currency, -amount_minor),
        ]
    return [
        ledger.Line(asset, account.currency, amount_minor),
        ledger.Line(counter, account.currency, -amount_minor),
    ]


def _parse_positive(db: Session, amount: str, currency: str) -> int:
    minor, _ = money.parse(db, amount, currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    return minor


@dataclass
class _Posting:
    amount_minor: int
    original_minor: int | None = None
    original_currency: str | None = None
    rate: Decimal | None = None
    method: str | None = None
    rate_id: uuid.UUID | None = None
    warnings: list[str] = field(default_factory=list)
    deviation: Decimal | None = None


def _posting(
    db: Session,
    gateway: fx.FxGateway | None,
    user: User,
    account: FinancialAccount,
    amount_text: str,
    currency: str,
    when: date,
    choice: conversion.Conversion | None,
) -> _Posting:
    """The amount to post in the account's currency, converted if the entry's differs."""
    if currency == account.currency:
        if choice is not None:
            raise ApiError(
                422, "conversion_not_needed", "The amount is already in the account's currency"
            )
        return _Posting(_parse_positive(db, amount_text, currency))
    if choice is None or gateway is None:
        raise ApiError(
            422,
            "conversion_required",
            "The amount is in another currency: choose how it converts",
            {"account_currency": account.currency},
        )
    original = _parse_positive(db, amount_text, currency)
    done = conversion.apply(
        db, gateway, user, original_minor=original, original_currency=currency,
        account_currency=account.currency, on=when, choice=choice,
    )  # fmt: skip
    return _Posting(
        done.posted_minor, original, currency, done.rate, done.method, done.rate_id,
        done.warnings, done.deviation,
    )  # fmt: skip


def _set_conversion(document: Transaction, posting: _Posting) -> None:
    document.amount_minor = posting.amount_minor
    document.original_amount_minor = posting.original_minor
    document.original_currency = posting.original_currency
    document.fx_rate = posting.rate
    document.fx_method = posting.method
    document.fx_rate_id = posting.rate_id


def create(
    db: Session,
    user: User,
    draft: Draft,
    *,
    via: str = "quick_add",
    gateway: fx.FxGateway | None = None,
) -> Recorded:
    if draft.type not in ("income", "expense"):
        raise ApiError(422, "validation_error", "Only income and expense are recorded here")
    account = lock_accounts(db, user, [draft.account_id])[draft.account_id]
    _check_active(account)
    category = owned_category(db, user, draft.category_id)
    _check_category(category, draft.type)
    when = _check_date(user, draft.effective_date)
    posting = _posting(
        db, gateway, user, account, draft.amount, draft.currency, when, draft.conversion
    )

    document = Transaction(
        user_id=user.id,
        type=draft.type,
        account_id=account.id,
        currency=account.currency,
        category_id=category.id,
        occurred_at=datetime.now(UTC),
        effective_date=when,
        description=draft.description,
        notes=draft.notes,
        created_via=via,
    )
    _set_conversion(document, posting)
    db.add(document)
    db.flush()
    ledger.post(
        db,
        document,
        _lines(db, user, draft.type, account, posting.amount_minor),
        category_id=category.id,
    )
    audit(db, f"transaction.{draft.type}.created", user.id, entity_type="transaction",
          entity_id=document.id, **_fx_audit(posting))  # fmt: skip
    return Recorded(document, posting.warnings)


# --- Refunds (FINANCIAL_RULES.md §4.4, §4.11)


@dataclass(frozen=True)
class RefundDraft:
    """Money back for an earlier purchase, as the person entered it."""

    amount: str
    currency: str
    account_id: uuid.UUID | None = None  # default: the purchase's account
    effective_date: date | None = None  # default: today; never before the purchase
    description: str | None = None
    notes: str | None = None
    conversion: conversion.Conversion | None = None


def _live_refunds_query(original_id: uuid.UUID) -> Any:
    return select(Transaction).where(
        Transaction.refunds_transaction_id == original_id,
        Transaction.type == "refund",
        Transaction.status == "posted",
    )


def refunded_minor(db: Session, original: Transaction) -> int:
    """What has been refunded so far, in the purchase's posted currency (live refunds only)."""
    return sum(r.amount_minor for r in db.scalars(_live_refunds_query(original.id)))


def refunds_of(db: Session, original: Transaction) -> list[Transaction]:
    """Every refund of a purchase, voided ones included, oldest first."""
    return list(
        db.scalars(
            select(Transaction)
            .where(Transaction.refunds_transaction_id == original.id, Transaction.type == "refund")
            .order_by(Transaction.effective_date, Transaction.created_at)
        )
    )


def _refund_lines(
    db: Session, user: User, account: FinancialAccount, amount_minor: int
) -> list[ledger.Line]:
    """Dr the account, Cr Expense: money back that reduces spending, never income (§4.4)."""
    asset = ledger.asset_account(db, account)
    expense = ledger.shared_account(db, user.id, "expense", account.currency)
    return [
        ledger.Line(asset, account.currency, amount_minor),
        ledger.Line(expense, account.currency, -amount_minor),
    ]


def refund(
    db: Session,
    user: User,
    original_id: uuid.UUID,
    draft: RefundDraft,
    *,
    gateway: fx.FxGateway | None = None,
) -> Recorded:
    """Records a full or partial refund of a posted expense.

    - Posted in an account in the purchase's posted currency, in the purchase's category, dated
      when the money came back: it reduces spending in the refund's period (§4.4, §6).
    - Entered in another currency (for example the receipt's), it converts like any entry (§4.6).
    - Refunds together never exceed the purchase's posted amount; there is no override.
    - The purchase stays as it was; the original row is locked so concurrent refunds serialise.
    """
    original = owned(db, user, original_id, lock=True)
    if original.type != "expense" or original.status != "posted":
        raise ApiError(409, "not_refundable", "Only a posted expense can be refunded")
    account_id = draft.account_id or original.account_id
    if account_id is None:
        raise ApiError(422, "validation_error", "Choose the account the money came back to")
    account = lock_accounts(db, user, [account_id])[account_id]
    _check_active(account)
    if account.currency != original.currency:
        raise ApiError(
            422,
            "refund_currency_mismatch",
            "A refund goes to an account in the purchase's currency",
            {"currency": original.currency},
        )
    when = _check_date(user, draft.effective_date)
    if when < original.effective_date:
        raise ApiError(422, "refund_before_purchase", "A refund can't be dated before the purchase")
    posting = _posting(
        db, gateway, user, account, draft.amount, draft.currency, when, draft.conversion
    )
    remaining = original.amount_minor - refunded_minor(db, original)
    if posting.amount_minor > remaining:
        exponent = money.currency(db, original.currency, for_new_records=False).exponent
        raise ApiError(
            409,
            "refund_exceeds_original",
            "That's more than is left to refund",
            {
                "refundable": money.to_amount(max(remaining, 0), exponent),
                "currency": original.currency,
            },
        )
    document = Transaction(
        user_id=user.id,
        type="refund",
        account_id=account.id,
        currency=account.currency,
        category_id=original.category_id,
        refunds_transaction_id=original.id,
        occurred_at=datetime.now(UTC),
        effective_date=when,
        description=draft.description or original.description,
        notes=draft.notes,
        created_via="form",
    )
    _set_conversion(document, posting)
    db.add(document)
    db.flush()
    ledger.post(
        db,
        document,
        _refund_lines(db, user, account, posting.amount_minor),
        category_id=original.category_id,
    )
    audit(db, "transaction.refund.created", user.id, entity_type="transaction",
          entity_id=document.id, refunds=str(original.id), **_fx_audit(posting))  # fmt: skip
    return Recorded(document, posting.warnings)


def _fx_audit(posting: _Posting) -> dict[str, str]:
    if posting.method is None:
        return {}
    details = {"fx_method": posting.method, "fx_rate": str(posting.rate)}
    if posting.deviation is not None:
        details["fx_deviation"] = str(posting.deviation.quantize(Decimal("0.0001")))
    return details


def owned(db: Session, user: User, transaction_id: uuid.UUID, *, lock: bool = False) -> Transaction:
    query = select(Transaction).where(
        Transaction.id == transaction_id, Transaction.user_id == user.id
    )
    if lock:
        query = query.with_for_update()
    found = db.scalar(query)
    if found is None:
        raise not_found()
    return found


def _check_version(document: Transaction, version: int) -> None:
    if document.version != version:
        raise ApiError(
            412,
            "version_conflict",
            "The transaction changed; reload it",
            {"version": document.version},
        )


def _uuid(value: object, current: uuid.UUID | None) -> uuid.UUID:
    """A changed reference if one was given, otherwise the current one."""
    chosen = value if value is not None else current
    if not isinstance(chosen, uuid.UUID):
        raise ApiError(422, "validation_error", "A reference is missing")
    return chosen


def _corrected_posting(
    db: Session,
    gateway: fx.FxGateway | None,
    user: User,
    document: Transaction,
    account: FinancialAccount,
    changes: dict[str, object],
    when: date,
) -> _Posting:
    """The replacement's amount. A stored conversion is reused unless the person gives a new one
    (FINANCIAL_RULES.md §7.4): nothing is ever re-rated at today's rate behind their back."""
    entered = str(changes.get("currency") or document.original_currency or document.currency)
    amount_text = changes.get("amount")
    choice = changes.get("conversion")
    if choice is not None and not isinstance(choice, conversion.Conversion):
        raise ApiError(422, "validation_error", "Invalid conversion")
    if amount_text is None:
        if entered == document.original_currency and document.original_amount_minor is not None:
            exponent = money.currency(db, entered, for_new_records=False).exponent
            amount_text = money.to_amount(document.original_amount_minor, exponent)
        elif entered == document.currency and document.original_amount_minor is None:
            exponent = money.currency(db, entered, for_new_records=False).exponent
            amount_text = money.to_amount(document.amount_minor, exponent)
        else:
            raise ApiError(
                422,
                "currency_mismatch",
                "Moving to an account in another currency needs the amount again",
                {"account_currency": account.currency},
            )
    if entered == account.currency:
        return _posting(db, gateway, user, account, str(amount_text), entered, when, choice)
    if choice is not None:
        return _posting(db, gateway, user, account, str(amount_text), entered, when, choice)

    # No new conversion: keep the stored one, for the same pair only.
    same_pair = entered == document.original_currency and account.currency == document.currency
    if not same_pair or document.fx_rate is None or document.fx_method is None:
        raise ApiError(
            422,
            "conversion_required",
            "Choose how this amount converts",
            {"account_currency": account.currency},
        )
    original = _parse_positive(db, str(amount_text), entered)
    if document.fx_method == "actual_amount":
        if original != document.original_amount_minor:
            raise ApiError(
                422,
                "conversion_required",
                "The amount charged was typed by hand: enter the new amount charged too",
                {"account_currency": account.currency},
            )
        posted = document.amount_minor
    else:
        from_exp = money.currency(db, entered, for_new_records=False).exponent
        to_exp = money.currency(db, account.currency, for_new_records=False).exponent
        posted = fx.convert_minor(original, from_exp, to_exp, document.fx_rate)
    return _Posting(
        posted, original, entered, document.fx_rate, document.fx_method, document.fx_rate_id
    )


def correct(
    db: Session,
    user: User,
    transaction_id: uuid.UUID,
    version: int,
    changes: dict[str, object],
    *,
    gateway: fx.FxGateway | None = None,
) -> Transaction:
    """Fixes a mistake in an income or expense. History is kept: see the module docstring."""
    document = owned(db, user, transaction_id, lock=True)
    _check_version(document, version)
    if document.status != "posted":
        raise ApiError(409, "invalid_state", "A voided transaction can't be changed")
    if document.type not in ("income", "expense"):
        raise ApiError(
            409, "not_correctable", "Only income and expenses can be edited; void it instead"
        )

    money_changes = MONEY_FIELDS & changes.keys()
    if {"amount", "account_id", "conversion"} & changes.keys() and _managed_by_plan(db, document):
        raise ApiError(
            409,
            "managed_by_installment_plan",
            "This purchase belongs to an installment plan: its amount and account are fixed. "
            "Change the description, or remove the plan and record it again",
        )
    if money_changes and document.type == "expense" and refunded_minor(db, document) > 0:
        raise ApiError(
            409,
            "has_refunds",
            "This purchase has refunds: void them first, or change only the description",
        )
    posting: _Posting | None = None
    if money_changes:
        account_id = _uuid(changes.get("account_id"), document.account_id)
        ids = [i for i in (document.account_id, account_id) if i is not None]
        accounts = lock_accounts(db, user, ids)
        account = accounts[account_id]
        if account.id != document.account_id:
            _check_active(account)
        category_id = _uuid(changes.get("category_id"), document.category_id)
        category = owned_category(db, user, category_id)
        # Keeping an archived category on its own transaction is fine; choosing one isn't.
        _check_category(category, document.type, allow_archived=category_id == document.category_id)
        when = changes.get("effective_date")
        when = _check_date(user, when) if isinstance(when, date) else document.effective_date
        posting = _corrected_posting(db, gateway, user, document, account, changes, when)

        live = ledger.live_entry(db, document)
        if live is not None:
            ledger.reverse(db, document, live)
        document.account_id = account.id
        document.currency = account.currency
        _set_conversion(document, posting)
        document.category_id = category.id
        document.effective_date = when
        db.flush()
        ledger.post(
            db,
            document,
            _lines(db, user, document.type, account, posting.amount_minor),
            category_id=category.id,
        )

    for name in ("description", "notes"):
        if name in changes:
            setattr(document, name, changes[name])
    document.version += 1
    db.flush()
    audit(db, "transaction.corrected", user.id, entity_type="transaction", entity_id=document.id,
          fields=",".join(sorted(changes)))  # fmt: skip
    return document


TRIP_TYPES = frozenset({"trip_expense_payment", "trip_settlement_paid", "trip_settlement_received"})


def flow_lines(
    db: Session, user: User, kind: str, account: FinancialAccount, amount_minor: int
) -> list[ledger.Line]:
    """The lines of a plain income or expense (for converting an unlinked trip entry back)."""
    return _lines(db, user, kind, account, amount_minor)


def void(
    db: Session,
    user: User,
    transaction_id: uuid.UUID,
    version: int,
    *,
    by_link: bool = False,
    by_plan: bool = False,
) -> Transaction:
    """Cancels a transaction's effect with a reversal. The document stays, marked reversed.

    A `trip_*` transaction belongs to its personal link: it's removed by unlinking (§11.9), so the
    link and the ledger can never disagree.
    """
    document = owned(db, user, transaction_id, lock=True)
    _check_version(document, version)
    if document.status != "posted":
        raise ApiError(409, "invalid_state", "This transaction is already voided")
    if document.type == "expense" and refunded_minor(db, document) > 0:
        raise ApiError(409, "has_refunds", "Void this purchase's refunds first")
    if document.type in TRIP_TYPES and not by_link:
        raise ApiError(
            409, "managed_by_trip_link", "This is recorded from a trip: unlink it there instead"
        )
    if not by_plan and _managed_by_plan(db, document):
        raise ApiError(
            409,
            "managed_by_installment_plan",
            "This belongs to an installment plan: remove the plan there instead",
        )
    if document.type == "debt_start" and document.debt_id is not None:
        debts.owned(db, user, document.debt_id, lock=True)
        if debts.has_live_payments(db, document.debt_id):
            raise ApiError(409, "debt_has_payments", "Void this debt's repayments before its start")
    ids = [i for i in (document.account_id, document.counter_account_id) if i is not None]
    if ids:
        lock_accounts(db, user, ids)
    live = ledger.live_entry(db, document)
    if live is not None:
        ledger.reverse(db, document, live)
    document.status = "reversed"
    document.version += 1
    db.flush()
    _reopen_occurrence(db, document)
    audit(db, "transaction.voided", user.id, entity_type="transaction", entity_id=document.id)
    return document


def _managed_by_plan(db: Session, document: Transaction) -> bool:
    """A plan's purchase and debt start move together (§4.13), so neither changes on its own."""
    from app.services import installments  # installments builds on this module

    return installments.plan_of_transaction(db, document) is not None


def _reopen_occurrence(db: Session, document: Transaction) -> None:
    """A confirmed recurring occurrence whose transaction is voided is open again (§5a)."""
    occurrence = db.scalar(
        select(RecurringOccurrence)
        .where(RecurringOccurrence.transaction_id == document.id)
        .with_for_update()
    )
    if occurrence is None:
        return
    occurrence.status = "pending"
    occurrence.transaction_id = None
    occurrence.decided_at = None
    occurrence.version += 1
    db.flush()


# --- History


@dataclass(frozen=True)
class Filters:
    type: str | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    search: str | None = None
    include_voided: bool = False
    currency: str | None = None


def _encode(row: Transaction) -> str:
    raw = json.dumps([row.effective_date.isoformat(), row.occurred_at.isoformat(), str(row.id)])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[date, datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        day, moment, ident = json.loads(base64.urlsafe_b64decode(padded))
        return date.fromisoformat(day), datetime.fromisoformat(moment), uuid.UUID(ident)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ApiError(400, "invalid_cursor", "The page cursor isn't valid") from None


def _filtered(user: User, filters: Filters) -> Any:
    """The person's transactions matching the filters (shared by the list and its count)."""
    query = select(Transaction).where(Transaction.user_id == user.id)
    if not filters.include_voided:
        query = query.where(Transaction.status == "posted")
    if filters.type is not None:
        query = query.where(Transaction.type == filters.type)
    if filters.account_id is not None:
        query = query.where(
            or_(
                Transaction.account_id == filters.account_id,
                Transaction.counter_account_id == filters.account_id,
            )
        )
    if filters.category_id is not None:
        query = query.where(Transaction.category_id == filters.category_id)
    if filters.currency is not None:
        query = query.where(Transaction.currency == filters.currency)
    if filters.date_from is not None:
        query = query.where(Transaction.effective_date >= filters.date_from)
    if filters.date_to is not None:
        query = query.where(Transaction.effective_date <= filters.date_to)
    if filters.search:
        # A literal search: % and _ typed by the person match themselves.
        term = filters.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        query = query.where(
            or_(
                Transaction.description.ilike(pattern, escape="\\"),
                Transaction.notes.ilike(pattern, escape="\\"),
            )
        )
    return query


def count(db: Session, user: User, filters: Filters) -> int:
    """How many transactions match, without loading them."""
    subquery = _filtered(user, filters).with_only_columns(Transaction.id).subquery()
    return int(db.scalar(select(func.count()).select_from(subquery)) or 0)


def history(
    db: Session, user: User, filters: Filters, *, limit: int, cursor: str | None
) -> tuple[list[Transaction], str | None]:
    """Newest first, keyset-paginated (API_SPEC.md §1.5): stable while new entries arrive."""
    query = _filtered(user, filters)
    if cursor:
        day, moment, ident = _decode(cursor)
        query = query.where(
            or_(
                Transaction.effective_date < day,
                and_(Transaction.effective_date == day, Transaction.occurred_at < moment),
                and_(
                    Transaction.effective_date == day,
                    Transaction.occurred_at == moment,
                    Transaction.id < ident,
                ),
            )
        )
    rows = list(
        db.scalars(
            query.order_by(
                Transaction.effective_date.desc(),
                Transaction.occurred_at.desc(),
                Transaction.id.desc(),
            ).limit(limit + 1)
        )
    )
    page = rows[:limit]
    return page, (_encode(page[-1]) if len(rows) > limit and page else None)


@dataclass(frozen=True)
class Change:
    at: datetime
    action: str  # created, corrected, voided


def changes_of(db: Session, document: Transaction) -> list[Change]:
    """What happened to a transaction, from its immutable entries: the first one created it; a
    reversal followed by a replacement corrected it; a final reversal alone voided it."""
    entries: Sequence[JournalEntry] = list(
        db.scalars(
            select(JournalEntry)
            .where(JournalEntry.transaction_id == document.id)
            .order_by(JournalEntry.posted_at, JournalEntry.id)
        )
    )
    timeline: list[Change] = []
    for index, entry in enumerate(entries):
        if index == 0:
            timeline.append(Change(entry.posted_at, "created"))
        elif entry.reverses_entry_id is not None:
            following = entries[index + 1] if index + 1 < len(entries) else None
            action = "corrected" if following and following.reverses_entry_id is None else "voided"
            timeline.append(Change(entry.posted_at, action))
    return timeline
