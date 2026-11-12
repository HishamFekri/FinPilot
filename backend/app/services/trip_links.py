"""Personal links: the owner's own bridge from a trip to their accounts (FINANCIAL_RULES.md §11).

The only module that reads both a trip and a personal ledger. Trip modules never import it
(import-linter), and nothing here is ever returned by a trip endpoint: links, dismissals and every
figure derived from them are visible to their owner only (§11.9, SECURITY.md §4.5).

- **Cash only (option A, §11.2 to §11.3).** A link posts the cash that really moved, through the
  owner's Trip Clearing account in the account's currency: `trip_expense_payment` and
  `trip_settlement_paid` (Dr Trip Clearing, Cr the account), `trip_settlement_received` (Dr the
  account, Cr Trip Clearing). None has a category or an income or expense line, so a payment is
  never counted as spending twice and a reimbursement can never be income. What a trip cost the
  person is their share, which `reporting` reads from the trip records.
- **Who links what (§11.6).** An approved expense: its payer only. A confirmed payment: its payer
  (as paid) or its recipient (as received). Shares and reversals: nobody. Only the owner of the
  account, and only on their own request: nothing posts as a side effect of a trip event (§11.9).
- **One live link** per (owner, expense), (owner, settlement) and personal transaction (database
  partial unique indexes). The item row is locked first, so concurrent requests serialise.
- **Review is computed, never stored.** A trip correction never writes anything personal, so an
  expense link needs review when the expense's approved version is not the one linked and its
  amount or date differs, or it was cancelled; a settlement link, when the payment has a confirmed
  reversal the owner hasn't kept. *Keep* moves the marker (`linked_version_id`, `reversal_kept_at`).
- **Currencies (§11.8).** The account amount is what the owner says was charged; the link keeps
  the trip base amount. When they differ in currency, the transaction's memo records the base
  amount and the implied rate of the two actual amounts (`actual_amount`): no rate is invented and
  history is never revalued.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    Category,
    DuplicateDismissal,
    ExpenseShare,
    FinancialAccount,
    PersonalLink,
    RecurringOccurrence,
    Settlement,
    Transaction,
    Trip,
    TripExpense,
    TripExpenseVersion,
    TripMember,
    User,
)
from app.services import fx, ledger, money, reporting
from app.services import transactions as txns
from app.services.accounts import today_for
from app.services.movements import lock_accounts
from app.services.sessions import audit

KIND_TYPE = {
    "expense_payment": "trip_expense_payment",
    "settlement_paid": "trip_settlement_paid",
    "settlement_received": "trip_settlement_received",
}
DUPLICATE_WINDOW_DAYS = 3
STRONG_WITHIN_DAYS = 1
NEAR_PERCENT = 5


def _need[T](value: T | None) -> T:
    """A row the data model guarantees; a real error (not an assert) if it's somehow missing."""
    if value is None:
        raise ApiError(409, "invalid_state", "Not allowed now")
    return value


def _trip(db: Session, user: User, trip_id: uuid.UUID) -> Trip:
    """A trip the person belongs or belonged to; 404 otherwise. Linking is their own money, so a
    former member can still record what they paid or received."""
    trip = db.get(Trip, trip_id)
    if trip is None or db.get(TripMember, (trip_id, user.id)) is None:
        raise ApiError(404, "not_found", "Trip not found")
    return trip


def _approved(db: Session, expense_id: uuid.UUID) -> TripExpenseVersion | None:
    """The expense's approved version, which may be a cancellation."""
    return db.scalar(
        select(TripExpenseVersion).where(
            TripExpenseVersion.expense_id == expense_id, TripExpenseVersion.state == "approved"
        )
    )


# --- What is being linked


@dataclass(frozen=True)
class Target:
    kind: str
    trip: Trip
    base_amount: int  # signed, trip base currency: + paid, - received
    date: date
    description: str
    item_currency: str  # the receipt's (or payment's) currency
    item_amount: int
    expense: TripExpense | None = None
    version: TripExpenseVersion | None = None
    settlement: Settlement | None = None


def _expense_target(
    db: Session, user: User, trip: Trip, expense_id: uuid.UUID, *, lock: bool
) -> Target:
    query = select(TripExpense).where(TripExpense.id == expense_id, TripExpense.trip_id == trip.id)
    expense = db.scalar(query.with_for_update() if lock else query)
    if expense is None:
        raise ApiError(404, "not_found", "Expense not found")
    if expense.payer_user_id != user.id:
        raise ApiError(403, "not_payer", "Only the person who paid can record this payment")
    version = _approved(db, expense.id)
    if version is None or version.kind == "cancellation":
        raise ApiError(409, "expense_not_approved", "Only an approved expense can be recorded")
    return Target(
        "expense_payment", trip, version.base_amount_minor, version.expense_date,
        version.description, version.currency, version.amount_minor, expense=expense,
        version=version,
    )  # fmt: skip


def reversal_of(db: Session, settlement_id: uuid.UUID) -> Settlement | None:
    return db.scalar(
        select(Settlement).where(
            Settlement.reverses_settlement_id == settlement_id, Settlement.state == "confirmed"
        )
    )


def _settlement_target(
    db: Session, user: User, trip: Trip, settlement_id: uuid.UUID, *, lock: bool
) -> Target:
    query = select(Settlement).where(Settlement.id == settlement_id, Settlement.trip_id == trip.id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(404, "not_found", "Payment not found")
    if user.id not in (row.payer_user_id, row.recipient_user_id):
        raise ApiError(403, "not_party", "Only the payer or the recipient can record this payment")
    if row.kind != "payment":
        raise ApiError(409, "reversal_not_linkable", "A reversal can't be recorded in an account")
    if row.state != "confirmed":
        raise ApiError(409, "settlement_not_confirmed", "Only a confirmed payment can be recorded")
    if reversal_of(db, row.id) is not None:
        raise ApiError(409, "settlement_reversed", "This payment was reversed")
    paid = row.payer_user_id == user.id
    return Target(
        "settlement_paid" if paid else "settlement_received", trip,
        row.base_amount_minor if paid else -row.base_amount_minor, row.paid_on, "",
        row.currency, row.amount_minor, settlement=row,
    )  # fmt: skip


def target(
    db: Session,
    user: User,
    trip_id: uuid.UUID,
    *,
    expense_id: uuid.UUID | None,
    settlement_id: uuid.UUID | None,
    lock: bool = False,
) -> Target:
    if (expense_id is None) == (settlement_id is None):
        raise ApiError(422, "validation_error", "Choose one expense or one payment")
    trip = _trip(db, user, trip_id)
    if expense_id is not None:
        return _expense_target(db, user, trip, expense_id, lock=lock)
    return _settlement_target(db, user, trip, _need(settlement_id), lock=lock)


def _live_for(db: Session, user: User, t: Target) -> PersonalLink | None:
    query = select(PersonalLink).where(
        PersonalLink.owner_user_id == user.id, PersonalLink.status == "active"
    )
    if t.expense is not None:
        query = query.where(PersonalLink.expense_id == t.expense.id)
    else:
        query = query.where(PersonalLink.settlement_id == _need(t.settlement).id)
    return db.scalar(query)


# --- Posting


def _lines(
    db: Session, user: User, kind: str, account: FinancialAccount, amount_minor: int
) -> list[ledger.Line]:
    asset = ledger.asset_account(db, account)
    clearing = ledger.shared_account(db, user.id, "trip_clearing", account.currency)
    out = 1 if kind != "settlement_received" else -1
    return [
        ledger.Line(clearing, account.currency, out * amount_minor),
        ledger.Line(asset, account.currency, -out * amount_minor),
    ]


def _memo(db: Session, document: Transaction, base_minor: int, base_currency: str) -> None:
    """Records the trip base amount and the implied rate when the account's currency differs."""
    if base_currency == document.currency:
        document.original_amount_minor = None
        document.original_currency = None
        document.fx_rate = None
        document.fx_method = None
        document.fx_rate_id = None
        return
    base_exp = money.currency(db, base_currency, for_new_records=False).exponent
    account_exp = money.currency(db, document.currency, for_new_records=False).exponent
    document.original_amount_minor = abs(base_minor)
    document.original_currency = base_currency
    document.fx_rate = fx.implied_rate(
        abs(base_minor), base_exp, document.amount_minor, account_exp
    )
    document.fx_method = "actual_amount"
    document.fx_rate_id = None


def _check_date(user: User, value: date) -> date:
    if value > today_for(user):
        raise ApiError(422, "date_in_future", "The date can't be in the future")
    return value


def _post(
    db: Session,
    user: User,
    t: Target,
    account: FinancialAccount,
    amount_minor: int,
    when: date,
    description: str | None,
    replaces: uuid.UUID | None = None,
) -> Transaction:
    if amount_minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    document = Transaction(
        user_id=user.id,
        type=KIND_TYPE[t.kind],
        account_id=account.id,
        currency=account.currency,
        amount_minor=amount_minor,
        occurred_at=datetime.now(UTC),
        effective_date=when,
        description=description,
        created_via="link",
        replaces_transaction_id=replaces,
    )
    _memo(db, document, t.base_amount, t.trip.base_currency)
    db.add(document)
    db.flush()
    ledger.post(db, document, _lines(db, user, t.kind, account, amount_minor))
    return document


def _insert_link(db: Session, user: User, t: Target, document: Transaction) -> PersonalLink:
    link = PersonalLink(
        owner_user_id=user.id,
        trip_id=t.trip.id,
        kind=t.kind,
        expense_id=t.expense.id if t.expense else None,
        settlement_id=t.settlement.id if t.settlement else None,
        transaction_id=document.id,
        linked_version_id=t.version.id if t.version else None,
        linked_base_amount_minor=t.base_amount,
        base_currency=t.trip.base_currency,
    )
    db.add(link)
    db.flush()
    return link


def _account(db: Session, user: User, account_id: uuid.UUID) -> FinancialAccount:
    account = lock_accounts(db, user, [account_id])[account_id]
    if account.status != "active":
        raise ApiError(409, "account_archived", "An archived account accepts no new entries")
    return account


def create(
    db: Session,
    user: User,
    trip_id: uuid.UUID,
    *,
    expense_id: uuid.UUID | None,
    settlement_id: uuid.UUID | None,
    account_id: uuid.UUID,
    amount: str,
    effective_date: date | None,
    description: str | None = None,
) -> PersonalLink:
    """Records a trip expense or payment in the owner's account (§11.11), in one transaction."""
    t = target(db, user, trip_id, expense_id=expense_id, settlement_id=settlement_id, lock=True)
    if _live_for(db, user, t) is not None:
        raise ApiError(409, "already_linked", "This is already recorded in your accounts")
    account = _account(db, user, account_id)
    minor, _ = money.parse(db, amount, account.currency)
    when = _check_date(user, effective_date or t.date)
    document = _post(db, user, t, account, minor, when, description or t.description or None)
    link = _insert_link(db, user, t, document)
    audit(db, "trip_link.created", user.id, entity_type="personal_link", entity_id=link.id,
          kind=t.kind)  # fmt: skip
    return link


def _plain_for(kind: str) -> str:
    return "income" if kind == "settlement_received" else "expense"


def attach(
    db: Session,
    user: User,
    trip_id: uuid.UUID,
    *,
    expense_id: uuid.UUID | None,
    settlement_id: uuid.UUID | None,
    transaction_id: uuid.UUID,
    version: int,
) -> PersonalLink:
    """ "Attach existing" (§11.7): voids the person's hand-entered expense (or income, for money
    received) and posts its `trip_*` replacement with the same account, amount and date. Only on
    their explicit confirmation; the balance doesn't change, and the original stays in history."""
    t = target(db, user, trip_id, expense_id=expense_id, settlement_id=settlement_id, lock=True)
    if _live_for(db, user, t) is not None:
        raise ApiError(409, "already_linked", "This is already recorded in your accounts")
    original = txns.owned(db, user, transaction_id, lock=True)
    if original.status != "posted" or original.type != _plain_for(t.kind):
        raise ApiError(
            409, "not_attachable", "Only a posted, hand-entered expense or income can be attached"
        )
    if db.scalar(
        select(RecurringOccurrence.id).where(RecurringOccurrence.transaction_id == original.id)
    ):
        raise ApiError(409, "not_attachable", "A confirmed recurring item can't be attached")
    account = _account(db, user, _need(original.account_id))
    txns.void(db, user, original.id, version)
    document = _post(
        db, user, t, account, original.amount_minor, original.effective_date,
        original.description or t.description or None, replaces=original.id,
    )  # fmt: skip
    link = _insert_link(db, user, t, document)
    audit(db, "trip_link.attached", user.id, entity_type="personal_link", entity_id=link.id,
          kind=t.kind, replaced=str(original.id))  # fmt: skip
    return link


# --- Review (computed, §11.9)


@dataclass(frozen=True)
class Review:
    needed: bool
    reason: str | None = None  # expense_changed, expense_cancelled, settlement_reversed
    current_base_amount: int | None = None
    current_date: date | None = None


def review(db: Session, link: PersonalLink) -> Review:
    if link.status != "active":
        return Review(False)
    if link.kind == "expense_payment":
        current = _approved(db, _need(link.expense_id))
        if current is None or current.id == link.linked_version_id:
            return Review(False)
        if current.kind == "cancellation":
            return Review(True, "expense_cancelled", 0, current.expense_date)
        linked = _need(db.get(TripExpenseVersion, link.linked_version_id))
        if (current.base_amount_minor, current.expense_date) == (
            linked.base_amount_minor,
            linked.expense_date,
        ):
            return Review(False)
        return Review(True, "expense_changed", current.base_amount_minor, current.expense_date)
    if link.reversal_kept_at is None and reversal_of(db, _need(link.settlement_id)) is not None:
        return Review(True, "settlement_reversed")
    return Review(False)


def owned(db: Session, user: User, link_id: uuid.UUID, *, lock: bool = False) -> PersonalLink:
    query = select(PersonalLink).where(
        PersonalLink.id == link_id, PersonalLink.owner_user_id == user.id
    )
    found = db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise ApiError(404, "not_found", "Not found")
    return found


def _lock_item(db: Session, link: PersonalLink) -> None:
    """Locks the trip row the link points at, so a resolve and a new link serialise."""
    if link.expense_id is not None:
        db.scalar(select(TripExpense).where(TripExpense.id == link.expense_id).with_for_update())
    else:
        db.scalar(select(Settlement).where(Settlement.id == link.settlement_id).with_for_update())


def _void_linked(db: Session, user: User, link: PersonalLink) -> Transaction:
    document = txns.owned(db, user, link.transaction_id, lock=True)
    if document.status == "posted":
        txns.void(db, user, document.id, document.version, by_link=True)
    return document


def _end(link: PersonalLink) -> None:
    link.status = "unlinked"
    link.resolved_at = datetime.now(UTC)


def resolve(
    db: Session,
    user: User,
    link_id: uuid.UUID,
    action: str,
    *,
    amount: str | None = None,
    effective_date: date | None = None,
) -> PersonalLink:
    """Update my transaction, keep as is, or reverse my transaction (§11.9). Only the owner."""
    link = owned(db, user, link_id, lock=True)
    if link.status != "active":
        raise ApiError(409, "link_not_active", "This link was already removed")
    _lock_item(db, link)
    state = review(db, link)
    if action == "keep":
        if not state.needed:
            return link  # nothing to keep: idempotent
        if link.kind == "expense_payment":
            current = _need(_approved(db, _need(link.expense_id)))
            link.linked_version_id = current.id  # the amount stays; the gap shows as unrecorded
        else:
            link.reversal_kept_at = datetime.now(UTC)
        db.flush()
        audit(db, "trip_link.kept", user.id, entity_type="personal_link", entity_id=link.id)
        return link
    if action == "reverse":
        _void_linked(db, user, link)
        _end(link)
        db.flush()
        audit(db, "trip_link.reversed", user.id, entity_type="personal_link", entity_id=link.id)
        return link
    if action != "update":
        raise ApiError(422, "validation_error", "Choose update, keep or reverse")
    if link.kind != "expense_payment" or state.reason != "expense_changed":
        raise ApiError(409, "nothing_to_update", "Keep it, or reverse your transaction")
    if amount is None:
        raise ApiError(422, "validation_error", "Enter the amount charged")
    current = _need(_approved(db, _need(link.expense_id)))
    document = txns.owned(db, user, link.transaction_id, lock=True)
    if document.status != "posted":
        raise ApiError(409, "invalid_state", "Your transaction was already voided")
    account = _account(db, user, _need(document.account_id))
    minor, _ = money.parse(db, amount, account.currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    when = _check_date(user, effective_date or current.expense_date)
    live = ledger.live_entry(db, document)
    if live is not None:
        ledger.reverse(db, document, live)
    document.amount_minor = minor
    document.effective_date = when
    trip = _need(db.get(Trip, link.trip_id))
    _memo(db, document, current.base_amount_minor, trip.base_currency)
    document.version += 1
    db.flush()
    ledger.post(db, document, _lines(db, user, link.kind, account, minor))
    link.linked_version_id = current.id
    link.linked_base_amount_minor = current.base_amount_minor
    db.flush()
    audit(db, "trip_link.updated", user.id, entity_type="personal_link", entity_id=link.id)
    return link


def _plain_category(
    db: Session, user: User, link: PersonalLink, document: Transaction
) -> uuid.UUID:
    """For "convert": the replaced entry's category if still usable, else the trip category's
    match, else Other expense or Other income."""
    kind = _plain_for(link.kind)
    if document.replaces_transaction_id is not None:
        before = db.get(Transaction, document.replaces_transaction_id)
        if before is not None and before.category_id is not None:
            category = db.get(Category, before.category_id)
            if category is not None and category.status == "active" and category.kind == kind:
                return category.id
    keys = ["other_income"] if kind == "income" else ["other_expense"]
    if link.kind == "expense_payment" and link.linked_version_id is not None:
        version = db.get(TripExpenseVersion, link.linked_version_id)
        if version is not None:
            keys.insert(0, version.category_key)
    for key in keys:
        found = db.scalar(
            select(Category.id).where(
                Category.user_id == user.id,
                Category.system_key == key,
                Category.kind == kind,
                Category.status == "active",
            )
        )
        if found is not None:
            return found
    raise ApiError(409, "no_category", "No active category to convert into")


def unlink(db: Session, user: User, link_id: uuid.UUID, personal_transaction: str) -> PersonalLink:
    """Removes a link (§11.9): reverse the personal transaction, or convert it back into a plain
    expense or income (void and create in one transaction, same account, amount and date)."""
    if personal_transaction not in ("reverse", "convert"):
        raise ApiError(422, "validation_error", "Choose reverse or convert")
    link = owned(db, user, link_id, lock=True)
    if link.status != "active":
        return link  # already removed: idempotent
    _lock_item(db, link)
    document = txns.owned(db, user, link.transaction_id, lock=True)
    if personal_transaction == "convert" and document.status == "posted":
        account = _account(db, user, _need(document.account_id))
        category_id = _plain_category(db, user, link, document)
        _void_linked(db, user, link)
        plain = Transaction(
            user_id=user.id,
            type=_plain_for(link.kind),
            account_id=account.id,
            currency=account.currency,
            amount_minor=document.amount_minor,
            category_id=category_id,
            occurred_at=datetime.now(UTC),
            effective_date=document.effective_date,
            description=document.description,
            created_via="link",
            replaces_transaction_id=document.id,
        )
        db.add(plain)
        db.flush()
        ledger.post(
            db, plain,
            txns.flow_lines(db, user, plain.type, account, plain.amount_minor),
            category_id=category_id,
        )  # fmt: skip
    else:
        _void_linked(db, user, link)
    _end(link)
    db.flush()
    audit(db, "trip_link.unlinked", user.id, entity_type="personal_link", entity_id=link.id,
          personal_transaction=personal_transaction)  # fmt: skip
    return link


# --- Listing


def links(
    db: Session, user: User, trip_id: uuid.UUID | None = None, *, active_only: bool = True
) -> list[PersonalLink]:
    query = select(PersonalLink).where(PersonalLink.owner_user_id == user.id)
    if trip_id is not None:
        query = query.where(PersonalLink.trip_id == trip_id)
    if active_only:
        query = query.where(PersonalLink.status == "active")
    return list(db.scalars(query.order_by(PersonalLink.created_at.desc(), PersonalLink.id)))


# --- Possible duplicates (§11.7)


@dataclass(frozen=True)
class Candidate:
    transaction: Transaction
    strength: str  # strong or possible
    match: str  # original, base, share, near_base


@dataclass(frozen=True)
class DuplicateGroup:
    trip: Trip
    version: TripExpenseVersion
    expense: TripExpense
    my_share: int  # base currency
    paid_by_me: bool
    candidates: list[Candidate]


def _match(
    txn: Transaction, version: TripExpenseVersion, share: ExpenseShare | None
) -> tuple[str, str] | None:
    days = abs((txn.effective_date - version.expense_date).days)
    strong_or = "strong" if days <= STRONG_WITHIN_DAYS else "possible"
    amounts = {(txn.currency, txn.amount_minor)}
    if txn.original_currency and txn.original_amount_minor:
        amounts.add((txn.original_currency, txn.original_amount_minor))
    if (version.currency, version.amount_minor) in amounts:
        return strong_or, "original"
    if (version.base_currency, version.base_amount_minor) in amounts:
        return strong_or, "base"
    if share is not None and (
        (version.currency, share.share_minor) in amounts
        or (version.base_currency, share.base_share_minor) in amounts
    ):
        return "possible", "share"
    # Charged in the base currency, a little off the snapshot conversion (card fees, rounding).
    if version.currency != version.base_currency and txn.currency == version.base_currency:
        gap = abs(txn.amount_minor - version.base_amount_minor)
        if gap * 100 <= version.base_amount_minor * NEAR_PERCENT:
            return "possible", "near_base"
    return None


def duplicates(
    db: Session, user: User, trip_id: uuid.UUID | None = None, expense_id: uuid.UUID | None = None
) -> list[DuplicateGroup]:
    """The person's own posted, hand-entered expenses that may be a trip expense they paid or
    share in. Never acted on automatically; "not a duplicate" is remembered per pair."""
    query = (
        select(TripExpenseVersion, TripExpense, Trip)
        .join(TripExpense, TripExpense.id == TripExpenseVersion.expense_id)
        .join(Trip, Trip.id == TripExpense.trip_id)
        .join(TripMember, (TripMember.trip_id == Trip.id) & (TripMember.user_id == user.id))
        .where(TripExpenseVersion.state == "approved", TripExpenseVersion.kind != "cancellation")
    )
    if trip_id is not None:
        query = query.where(Trip.id == trip_id)
    if expense_id is not None:
        query = query.where(TripExpense.id == expense_id)
    rows = list(db.execute(query))
    if not rows:
        return []
    my_shares = {
        s.version_id: s
        for s in db.scalars(
            select(ExpenseShare).where(
                ExpenseShare.user_id == user.id,
                ExpenseShare.version_id.in_([v.id for v, _, _ in rows]),
            )
        )
    }
    rows = [r for r in rows if r[1].payer_user_id == user.id or r[0].id in my_shares]
    if not rows:
        return []
    window = timedelta(days=DUPLICATE_WINDOW_DAYS)
    earliest = min(v.expense_date for v, _, _ in rows) - window
    latest = max(v.expense_date for v, _, _ in rows) + window
    plain = list(
        db.scalars(
            select(Transaction).where(
                Transaction.user_id == user.id,
                Transaction.type == "expense",
                Transaction.status == "posted",
                Transaction.effective_date >= earliest,
                Transaction.effective_date <= latest,
            )
        )
    )
    dismissed = {
        (txn_id, exp_id)
        for txn_id, exp_id in db.execute(
            select(DuplicateDismissal.transaction_id, DuplicateDismissal.expense_id).where(
                DuplicateDismissal.user_id == user.id
            )
        )
    }
    out = []
    for version, expense, trip in rows:
        share = my_shares.get(version.id)
        found = []
        for txn in plain:
            if abs((txn.effective_date - version.expense_date).days) > DUPLICATE_WINDOW_DAYS:
                continue
            if (txn.id, expense.id) in dismissed:
                continue
            hit = _match(txn, version, share)
            if hit is not None:
                found.append(Candidate(txn, *hit))
        if found:
            found.sort(key=lambda c: (c.strength != "strong", c.transaction.effective_date))
            out.append(
                DuplicateGroup(
                    trip=trip,
                    version=version,
                    expense=expense,
                    my_share=share.base_share_minor if share else 0,
                    paid_by_me=expense.payer_user_id == user.id,
                    candidates=found,
                )
            )
    return out


def dismiss(db: Session, user: User, transaction_id: uuid.UUID, expense_id: uuid.UUID) -> None:
    """ "Not a duplicate": never suggested again for this pair. Changes no figure."""
    txns.owned(db, user, transaction_id)
    expense = db.get(TripExpense, expense_id)
    if expense is None or db.get(TripMember, (expense.trip_id, user.id)) is None:
        raise ApiError(404, "not_found", "Expense not found")
    db.execute(
        insert(DuplicateDismissal)
        .values(user_id=user.id, transaction_id=transaction_id, expense_id=expense_id)
        .on_conflict_do_nothing()
    )
    audit(db, "trip_link.duplicate_dismissed", user.id, entity_type="transaction",
          entity_id=transaction_id)  # fmt: skip


# --- The person's position in a trip (§11.4), owner-only


@dataclass
class Position:
    trip: Trip
    paid: int = 0  # expenses I paid, base currency
    share: int = 0  # my allocated share (what the trip cost me)
    sent: int = 0  # confirmed payments I made
    received: int = 0  # confirmed payments I received
    pending_sent: int = 0
    pending_received: int = 0
    linked: int = 0
    needs_review: int = 0
    links: int = 0
    shares: list[reporting.ShareLine] = field(default_factory=list)

    @property
    def net(self) -> int:
        """Positive: others owe me. Negative: I owe."""
        return self.paid - self.share + self.sent - self.received

    @property
    def cash_position(self) -> int:
        """What I put into the trip, net: paid + sent - received = share + net (§11.4)."""
        return self.paid + self.sent - self.received

    @property
    def unrecorded(self) -> int:
        return self.cash_position - self.linked


def position(db: Session, user: User, trip_id: uuid.UUID) -> Position:
    """My figures in one trip, from the trip records and my own links only."""
    trip = _trip(db, user, trip_id)
    out = Position(trip)
    effective = list(
        db.execute(
            select(TripExpenseVersion, TripExpense.payer_user_id)
            .join(TripExpense, TripExpense.id == TripExpenseVersion.expense_id)
            .where(
                TripExpense.trip_id == trip.id,
                TripExpenseVersion.state == "approved",
                TripExpenseVersion.kind != "cancellation",
            )
        )
    )
    for version, payer in effective:
        if payer == user.id:
            out.paid += version.base_amount_minor
    out.share = sum(
        int(s)
        for s in db.scalars(
            select(ExpenseShare.base_share_minor).where(
                ExpenseShare.user_id == user.id,
                ExpenseShare.version_id.in_([v.id for v, _ in effective] or [uuid.uuid4()]),
            )
        )
    )
    for row in db.scalars(
        select(Settlement).where(
            Settlement.trip_id == trip.id,
            Settlement.state.in_(("confirmed", "pending")),
            (Settlement.payer_user_id == user.id) | (Settlement.recipient_user_id == user.id),
        )
    ):
        mine_sent = row.payer_user_id == user.id
        if row.state == "confirmed":
            if mine_sent:
                out.sent += row.base_amount_minor
            else:
                out.received += row.base_amount_minor
        elif mine_sent:
            out.pending_sent += row.base_amount_minor
        else:
            out.pending_received += row.base_amount_minor
    for link in links(db, user, trip.id):
        out.links += 1
        out.linked += link.linked_base_amount_minor
        if review(db, link).needed:
            out.needs_review += 1
    out.shares = [
        s for s in reporting.share_lines(db, user, date.min, date.max) if s.trip_id == trip.id
    ]
    return out
