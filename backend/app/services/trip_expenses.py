"""Shared trip expenses: versions, confirmation, shares, balances (FINANCIAL_RULES.md §8, §9, §12).

**The one rule (§12.1):** a version is approved at once if and only if its proposer is the payer;
otherwise the payer must confirm that specific version, by id. Nobody else can approve, not even
the owner (D10). Only the effective version (the approved one, unless it's a cancellation) counts.

- A correction never edits a version: it adds one. The current approved version keeps counting
  until a correction is approved; a rejected correction changes nothing (§12.3).
- One pending version per expense at most (409 `correction_already_pending`).
- A cancellation is a version without shares; once approved the expense is closed (§12.4).
- The payer never changes (§12.5): cancel and record a new expense instead.

**Money:** the original total is split by the chosen method, converted once to the trip's base
currency, and the base total is allocated in proportion to the original shares (§8.1), so both
sets of shares sum exactly. The database checks the sums again at commit (INV-09).

**Balances are derived on read (§9.2):** net = paid - owed (+ sent - received once settlements
exist). The nets always sum to exactly zero; no tolerance is needed.

Trip modules never import personal-finance modules (see `trips`).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    TRIP_CATEGORY_KEYS,
    ExpenseShare,
    FxRate,
    Settlement,
    Trip,
    TripDispute,
    TripExpense,
    TripExpenseVersion,
    TripMember,
    User,
)
from app.services import allocation, conversion, fx, money
from app.services.text_safety import unsafe
from app.services.trips import Access, active_member, event, require_active, require_status

DESCRIPTION_MAX = 120
NOTES_MAX = 500
REASON_MAX = 500


# --- Input and computed content


@dataclass(frozen=True)
class RateChoice:
    rate_id: uuid.UUID | None = None
    manual_rate: str | None = None
    accept_stale: bool = False


@dataclass(frozen=True)
class ExpenseInput:
    description: str
    category_key: str
    expense_date: date
    amount: str
    currency: str
    split_method: str
    shares: list[allocation.ShareInput]
    notes: str | None = None
    rate: RateChoice | None = None


@dataclass
class Computed:
    description: str
    notes: str | None
    category_key: str
    expense_date: date
    amount_minor: int
    currency: str
    base_currency: str
    base_amount_minor: int
    rate: Decimal | None
    rate_id: uuid.UUID | None
    rate_manual: bool
    split_method: str
    shares: list[allocation.Share]
    base_shares: dict[uuid.UUID, int]
    warnings: list[str] = field(default_factory=list)


def _text(value: str | None, name: str, limit: int, *, required: bool = False) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise ApiError(422, "validation_error", f"The {name} is required")
        return None
    if len(text) > limit or unsafe(text, allow_newline=True):
        raise ApiError(422, "validation_error", f"The {name} is too long or has hidden characters")
    return text


def _compute(
    db: Session,
    gateway: fx.FxGateway,
    user: User,
    trip: Trip,
    expense_id: uuid.UUID,
    data: ExpenseInput,
    *,
    allowed_inactive: set[uuid.UUID],
    record_manual: bool,
) -> Computed:
    if data.category_key not in TRIP_CATEGORY_KEYS:
        raise ApiError(422, "validation_error", "Choose a category")
    amount_minor, info = money.parse(db, data.amount, data.currency)
    if amount_minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    for share in data.shares:
        member = db.get(TripMember, (trip.id, share.user_id))
        if member is None:
            raise ApiError(422, "beneficiary_not_active_member", "Someone isn't on this trip")
        if member.status != "active" and share.user_id not in allowed_inactive:
            raise ApiError(
                422, "beneficiary_not_active_member", "Only current members can be added to it"
            )
    shares = allocation.split(
        amount_minor, info.exponent, data.split_method, data.shares, expense_id
    )

    rate: Decimal | None = None
    rate_id: uuid.UUID | None = None
    manual = False
    warnings: list[str] = []
    if data.currency == trip.base_currency:
        if data.rate and (data.rate.rate_id or data.rate.manual_rate):
            raise ApiError(
                422, "conversion_not_needed", "This amount is already in the base currency"
            )
        base_minor = amount_minor
    else:
        choice = data.rate or RateChoice()
        if choice.manual_rate:
            manual = True
            if record_manual:
                converted = conversion.apply(
                    db,
                    gateway,
                    user,
                    original_minor=amount_minor,
                    original_currency=data.currency,
                    account_currency=trip.base_currency,
                    on=data.expense_date,
                    choice=conversion.Conversion("manual_rate", rate=choice.manual_rate),
                )
                base_minor, rate, rate_id = (
                    converted.posted_minor,
                    converted.rate,
                    converted.rate_id,
                )
                warnings = converted.warnings
            else:
                rate = fx.parse_manual_rate(choice.manual_rate)
                base_exp = money.currency(db, trip.base_currency, for_new_records=False).exponent
                base_minor = fx.convert_minor(amount_minor, info.exponent, base_exp, rate)
        elif choice.rate_id:
            converted = conversion.apply(
                db,
                gateway,
                user,
                original_minor=amount_minor,
                original_currency=data.currency,
                account_currency=trip.base_currency,
                on=data.expense_date,
                choice=conversion.Conversion(
                    "reference", rate_id=choice.rate_id, accept_stale=choice.accept_stale
                ),
            )
            base_minor, rate, rate_id = converted.posted_minor, converted.rate, converted.rate_id
        else:
            raise ApiError(
                422,
                "rate_required",
                "Choose an exchange rate to the trip's currency",
                {"from": data.currency, "to": trip.base_currency},
            )
    return Computed(
        description=_text(data.description, "description", DESCRIPTION_MAX, required=True) or "",
        notes=_text(data.notes, "note", NOTES_MAX),
        category_key=data.category_key,
        expense_date=data.expense_date,
        amount_minor=amount_minor,
        currency=data.currency,
        base_currency=trip.base_currency,
        base_amount_minor=base_minor,
        rate=rate,
        rate_id=rate_id,
        rate_manual=manual,
        split_method=data.split_method,
        shares=shares,
        base_shares=allocation.base_shares(base_minor, shares, expense_id),
        warnings=warnings,
    )


def preview(
    db: Session, gateway: fx.FxGateway, user: User, a: Access, data: ExpenseInput
) -> Computed:
    """The shares an expense would get. Writes nothing (a typed rate isn't stored here)."""
    require_active(a)
    return _compute(
        db, gateway, user, a.trip, uuid.uuid4(), data, allowed_inactive=set(), record_manual=False
    )


def _insert_version(
    db: Session,
    expense: TripExpense,
    *,
    number: int,
    kind: str,
    state: str,
    proposer: uuid.UUID,
    content: Computed | TripExpenseVersion,
) -> TripExpenseVersion:
    now = datetime.now(UTC)
    version = TripExpenseVersion(
        expense_id=expense.id,
        trip_id=expense.trip_id,
        version_no=number,
        kind=kind,
        state=state,
        proposed_by=proposer,
        description=content.description,
        notes=content.notes,
        category_key=content.category_key,
        expense_date=content.expense_date,
        amount_minor=content.amount_minor,
        currency=content.currency,
        base_currency=content.base_currency,
        base_amount_minor=content.base_amount_minor,
        rate_id=content.rate_id,
        split_method=content.split_method,
        decided_by=proposer if state == "approved" else None,
        decided_at=now if state == "approved" else None,
    )
    db.add(version)
    db.flush()
    if kind != "cancellation" and isinstance(content, Computed):
        for share in content.shares:
            db.add(
                ExpenseShare(
                    version_id=version.id,
                    user_id=share.user_id,
                    trip_id=expense.trip_id,
                    input_value=share.input_value,
                    share_minor=share.share_minor,
                    base_share_minor=content.base_shares[share.user_id],
                )
            )
        db.flush()
    return version


# --- Reading versions


def versions(db: Session, expense_id: uuid.UUID) -> list[TripExpenseVersion]:
    return list(
        db.scalars(
            select(TripExpenseVersion)
            .where(TripExpenseVersion.expense_id == expense_id)
            .order_by(TripExpenseVersion.version_no)
        )
    )


def shares_of(db: Session, version_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ExpenseShare]]:
    out: dict[uuid.UUID, list[ExpenseShare]] = defaultdict(list)
    if version_ids:
        for row in db.scalars(select(ExpenseShare).where(ExpenseShare.version_id.in_(version_ids))):
            out[row.version_id].append(row)
    return out


def status_of(history: list[TripExpenseVersion]) -> str:
    """§12.2, derived from the versions; never stored."""
    approved = next((v for v in history if v.state == "approved"), None)
    if approved is not None:
        return "cancelled" if approved.kind == "cancellation" else "approved"
    latest = history[-1]
    return {"pending": "pending_payer", "rejected": "rejected"}.get(latest.state, "cancelled")


def classification(payer: uuid.UUID, beneficiaries: set[uuid.UUID]) -> str:
    """§8.5: personal, paid for others, or shared."""
    if beneficiaries == {payer}:
        return "personal"
    if payer not in beneficiaries:
        return "paid_for_others"
    return "shared"


# --- Writing


def _expense(db: Session, trip_id: uuid.UUID, expense_id: uuid.UUID) -> TripExpense:
    row = db.scalar(
        select(TripExpense)
        .where(TripExpense.id == expense_id, TripExpense.trip_id == trip_id)
        .with_for_update()
    )
    if row is None:
        raise ApiError(404, "not_found", "Expense not found")
    return row


def create(
    db: Session,
    gateway: fx.FxGateway,
    user: User,
    a: Access,
    *,
    payer_id: uuid.UUID,
    data: ExpenseInput,
    replaces: uuid.UUID | None = None,
) -> tuple[TripExpense, TripExpenseVersion, Computed]:
    require_active(a)
    require_status(a.trip, "active")
    if active_member(db, a.trip.id, payer_id) is None:
        raise ApiError(422, "payer_not_active_member", "The payer must be a current member")
    if replaces is not None:
        old = db.get(TripExpense, replaces)
        if (
            old is None
            or old.trip_id != a.trip.id
            or status_of(versions(db, old.id)) != "cancelled"
        ):
            raise ApiError(422, "validation_error", "It can only replace a cancelled expense")
    expense = TripExpense(
        trip_id=a.trip.id, payer_user_id=payer_id, created_by=user.id, replaces_expense_id=replaces
    )
    db.add(expense)
    db.flush()
    content = _compute(
        db, gateway, user, a.trip, expense.id, data, allowed_inactive=set(), record_manual=True
    )
    state = "approved" if payer_id == user.id else "pending"
    version = _insert_version(
        db, expense, number=1, kind="initial", state=state, proposer=user.id, content=content
    )
    event(
        db,
        a.trip.id,
        user.id,
        "expense.created" if state == "approved" else "expense.proposed",
        entity_type="trip_expense",
        entity_id=expense.id,
        version="1",
    )
    return expense, version, content


def _can_propose(a: Access, expense: TripExpense, user: User) -> None:
    require_active(a)
    if not (a.admin or user.id in (expense.created_by, expense.payer_user_id)):
        raise ApiError(403, "forbidden", "Only the payer, the person who added it, or an admin")


def _supersede_current(db: Session, history: list[TripExpenseVersion]) -> None:
    current = next((v for v in history if v.state == "approved"), None)
    if current is not None:
        current.state = "superseded"
        db.flush()  # before the new one is approved: one approved version at a time
        # Open disputes on the replaced version close (§12.8); the raisers see it on the
        # expense and in the activity feed, and can dispute the new version.
        now = datetime.now(UTC)
        for dispute in db.scalars(
            select(TripDispute).where(
                TripDispute.version_id == current.id, TripDispute.status == "open"
            )
        ):
            dispute.status = "closed_superseded"
            dispute.decided_at = now
            event(
                db,
                current.trip_id,
                None,
                "dispute.closed_superseded",
                entity_type="trip_expense",
                entity_id=current.expense_id,
            )
        db.flush()


def propose(
    db: Session,
    gateway: fx.FxGateway,
    user: User,
    a: Access,
    expense_id: uuid.UUID,
    *,
    data: ExpenseInput | None,
) -> tuple[TripExpense, TripExpenseVersion]:
    """A correction (`data`) or a cancellation (`data` is None)."""
    require_status(a.trip, "active")
    expense = _expense(db, a.trip.id, expense_id)
    _can_propose(a, expense, user)
    history = versions(db, expense.id)
    status = status_of(history)
    if status == "cancelled" and any(v.state == "approved" for v in history):
        raise ApiError(409, "expense_closed", "This expense was cancelled; record a new one")
    pending = next((v for v in history if v.state == "pending"), None)
    if pending is not None:
        raise ApiError(
            409,
            "correction_already_pending",
            "A change is already waiting for the payer",
            {"version_id": str(pending.id)},
        )
    current = next((v for v in history if v.state == "approved"), None)
    if data is None and current is None:
        raise ApiError(409, "invalid_state", "Only an approved expense can be cancelled")
    by_payer = user.id == expense.payer_user_id
    number = history[-1].version_no + 1
    if data is None:
        if current is None:  # pragma: no cover - refused above
            raise ApiError(409, "invalid_state", "Only an approved expense can be cancelled")
        if by_payer:
            _supersede_current(db, history)
        version = _insert_version(
            db,
            expense,
            number=number,
            kind="cancellation",
            state="approved" if by_payer else "pending",
            proposer=user.id,
            content=current,
        )
        action = "expense.cancelled" if by_payer else "expense.cancellation_proposed"
    else:
        kept = (
            {s.user_id for s in shares_of(db, [current.id]).get(current.id, [])}
            if current
            else set()
        )
        content = _compute(
            db, gateway, user, a.trip, expense.id, data, allowed_inactive=kept, record_manual=True
        )
        if by_payer:
            _supersede_current(db, history)
        version = _insert_version(
            db,
            expense,
            number=number,
            kind="correction",
            state="approved" if by_payer else "pending",
            proposer=user.id,
            content=content,
        )
        action = "expense.corrected" if by_payer else "expense.correction_proposed"
    event(
        db, a.trip.id, user.id, action, entity_type="trip_expense", entity_id=expense.id,
        version=str(number),
    )  # fmt: skip
    return expense, version


def _version(
    db: Session, a: Access, version_id: uuid.UUID
) -> tuple[TripExpense, TripExpenseVersion, list[TripExpenseVersion]]:
    version = db.get(TripExpenseVersion, version_id)
    if version is None or version.trip_id != a.trip.id:
        raise ApiError(404, "not_found", "Expense version not found")
    expense = _expense(db, a.trip.id, version.expense_id)
    db.refresh(version)
    if version.state != "pending":
        raise ApiError(409, "version_not_pending", "This change was already decided")
    return expense, version, versions(db, expense.id)


def decide(
    db: Session, user: User, a: Access, version_id: uuid.UUID, action: str, reason: str | None
) -> TripExpenseVersion:
    """approve or reject (the payer only, current or former), withdraw (proposer or admin)."""
    expense, version, history = _version(db, a, version_id)
    if action in ("approve", "reject"):
        if user.id != expense.payer_user_id:
            raise ApiError(403, "not_payer", "Only the payer can confirm or reject this")
    else:
        require_active(a)
        if not (a.admin or user.id == version.proposed_by):
            raise ApiError(403, "forbidden", "Only the person who proposed it, or an admin")
    if action == "approve":
        _supersede_current(db, history)
    version.state = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[action]
    version.decided_by = user.id
    version.decided_at = datetime.now(UTC)
    version.decision_reason = _text(reason, "reason", REASON_MAX) if action == "reject" else None
    db.flush()
    event(
        db,
        a.trip.id,
        user.id,
        f"expense.version.{version.state}",
        entity_type="trip_expense",
        entity_id=expense.id,
        version=str(version.version_no),
    )
    return version


# --- Balances (§9)


@dataclass(frozen=True)
class Effective:
    expense: TripExpense
    version: TripExpenseVersion
    shares: list[ExpenseShare]


def effective(db: Session, trip_id: uuid.UUID) -> list[Effective]:
    """The one counting version of each expense: approved, not a cancellation."""
    rows = list(
        db.execute(
            select(TripExpense, TripExpenseVersion)
            .join(TripExpenseVersion, TripExpenseVersion.expense_id == TripExpense.id)
            .where(
                TripExpense.trip_id == trip_id,
                TripExpenseVersion.state == "approved",
                TripExpenseVersion.kind != "cancellation",
            )
            .order_by(TripExpenseVersion.expense_date.desc(), TripExpense.created_at.desc())
        )
    )
    shares = shares_of(db, [v.id for _, v in rows])
    return [Effective(e, v, shares.get(v.id, [])) for e, v in rows]


@dataclass
class MemberBalance:
    user_id: uuid.UUID
    paid: int = 0
    owed: int = 0
    sent: int = 0  # confirmed settlements this member paid
    received: int = 0  # confirmed settlements this member received

    @property
    def net(self) -> int:
        """§9.2: paid - owed + sent - received. Positive: others owe this member."""
        return self.paid - self.owed + self.sent - self.received


def balances(db: Session, trip: Trip) -> dict[uuid.UUID, MemberBalance]:
    """Paid, owed and net per member, in the base currency's minor units."""
    out: dict[uuid.UUID, MemberBalance] = {
        m.user_id: MemberBalance(m.user_id)
        for m in db.scalars(select(TripMember).where(TripMember.trip_id == trip.id))
    }
    for item in effective(db, trip.id):
        out[item.expense.payer_user_id].paid += item.version.base_amount_minor
        for share in item.shares:
            out[share.user_id].owed += share.base_share_minor
    # Confirmed settlements only. A reversal is a confirmed settlement the other way, so the
    # formula nets it with no special case (§9.2).
    for payer, recipient, amount in db.execute(
        select(
            Settlement.payer_user_id, Settlement.recipient_user_id, Settlement.base_amount_minor
        ).where(Settlement.trip_id == trip.id, Settlement.state == "confirmed")
    ):
        out[payer].sent += int(amount)
        out[recipient].received += int(amount)
    return out


def nets(db: Session, trip: Trip) -> dict[uuid.UUID, int]:
    return {uid: b.net for uid, b in balances(db, trip).items()}


def pending_involving(db: Session, trip_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """A pending version where the user is the payer, the proposer, or a beneficiary."""
    pending = list(
        db.execute(
            select(TripExpenseVersion.id, TripExpenseVersion.proposed_by, TripExpense.payer_user_id)
            .join(TripExpense, TripExpense.id == TripExpenseVersion.expense_id)
            .where(TripExpenseVersion.trip_id == trip_id, TripExpenseVersion.state == "pending")
        )
    )
    if any(user_id in (proposer, payer) for _, proposer, payer in pending):
        return True
    shares = shares_of(db, [vid for vid, _, _ in pending])
    if any(s.user_id == user_id for rows in shares.values() for s in rows):
        return True
    return pending_settlements(db, trip_id, user_id) > 0


def pending_settlements(db: Session, trip_id: uuid.UUID, user_id: uuid.UUID | None = None) -> int:
    """Pending settlements in a trip (involving `user_id` when given)."""
    query = select(func.count()).where(Settlement.trip_id == trip_id, Settlement.state == "pending")
    if user_id is not None:
        query = query.where(
            (Settlement.payer_user_id == user_id) | (Settlement.recipient_user_id == user_id)
        )
    return int(db.scalar(query) or 0)


# --- Listing and detail


@dataclass(frozen=True)
class ExpenseView:
    expense: TripExpense
    history: list[TripExpenseVersion]
    shares: dict[uuid.UUID, list[ExpenseShare]]
    rates: dict[uuid.UUID, FxRate]

    @property
    def current(self) -> TripExpenseVersion | None:
        return next((v for v in self.history if v.state == "approved"), None)

    @property
    def pending(self) -> TripExpenseVersion | None:
        return next((v for v in self.history if v.state == "pending"), None)

    @property
    def shown(self) -> TripExpenseVersion:
        """What a list shows: the approved version, else the latest."""
        return self.current or self.history[-1]

    @property
    def status(self) -> str:
        return status_of(self.history)

    def involves(self, user_id: uuid.UUID) -> bool:
        if user_id in (self.expense.payer_user_id, self.expense.created_by):
            return True
        if any(v.proposed_by == user_id for v in self.history):
            return True
        return any(s.user_id == user_id for rows in self.shares.values() for s in rows)


def _views(db: Session, expenses: list[TripExpense]) -> list[ExpenseView]:
    if not expenses:
        return []
    all_versions = list(
        db.scalars(
            select(TripExpenseVersion)
            .where(TripExpenseVersion.expense_id.in_([e.id for e in expenses]))
            .order_by(TripExpenseVersion.version_no)
        )
    )
    by_expense: dict[uuid.UUID, list[TripExpenseVersion]] = defaultdict(list)
    for v in all_versions:
        by_expense[v.expense_id].append(v)
    shares = shares_of(db, [v.id for v in all_versions])
    rate_ids = {v.rate_id for v in all_versions if v.rate_id}
    rates = (
        {r.id: r for r in db.scalars(select(FxRate).where(FxRate.id.in_(rate_ids)))}
        if rate_ids
        else {}
    )
    return [
        ExpenseView(
            e, by_expense[e.id], {v.id: shares.get(v.id, []) for v in by_expense[e.id]}, rates
        )
        for e in expenses
    ]


def list_views(db: Session, a: Access) -> list[ExpenseView]:
    """Newest first. A former member sees only expenses they're part of (SECURITY.md §4.4)."""
    expenses = list(
        db.scalars(
            select(TripExpense)
            .where(TripExpense.trip_id == a.trip.id)
            .order_by(TripExpense.created_at.desc())
        )
    )
    views = _views(db, expenses)
    views.sort(key=lambda v: (v.shown.expense_date, v.expense.created_at), reverse=True)
    if not a.active:
        views = [v for v in views if v.involves(a.member.user_id)]
    return views


def view(db: Session, a: Access, expense_id: uuid.UUID) -> ExpenseView:
    expense = db.scalar(
        select(TripExpense).where(TripExpense.id == expense_id, TripExpense.trip_id == a.trip.id)
    )
    if expense is None:
        raise ApiError(404, "not_found", "Expense not found")
    result = _views(db, [expense])[0]
    if not a.active and not result.involves(a.member.user_id):
        raise ApiError(404, "not_found", "Expense not found")
    return result


def waiting_on(db: Session, trip_id: uuid.UUID, user_id: uuid.UUID) -> list[TripExpenseVersion]:
    """Pending versions this person, as payer, has to confirm or reject."""
    return list(
        db.scalars(
            select(TripExpenseVersion)
            .join(TripExpense, TripExpense.id == TripExpenseVersion.expense_id)
            .where(
                TripExpenseVersion.trip_id == trip_id,
                TripExpenseVersion.state == "pending",
                TripExpense.payer_user_id == user_id,
            )
            .order_by(TripExpenseVersion.created_at)
        )
    )


def waiting_counts(db: Session, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Per trip, how many changes wait on this person as payer (for the trip list)."""
    rows = db.execute(
        select(TripExpenseVersion.trip_id, func.count())
        .join(TripExpense, TripExpense.id == TripExpenseVersion.expense_id)
        .where(TripExpenseVersion.state == "pending", TripExpense.payer_user_id == user_id)
        .group_by(TripExpenseVersion.trip_id)
    )
    return {tid: int(n) for tid, n in rows}


# --- Dashboard (PRD.md §4.6)


@dataclass
class Dashboard:
    total: int
    shared: int
    personal: int
    categories: dict[str, int]
    balances: dict[uuid.UUID, MemberBalance]
    suggestions: list[tuple[uuid.UUID, uuid.UUID, int]]
    pending_count: int


def dashboard(db: Session, trip: Trip) -> Dashboard:
    items = effective(db, trip.id)
    total = shared = personal = 0
    categories: dict[str, int] = defaultdict(int)
    for item in items:
        amount = item.version.base_amount_minor
        total += amount
        categories[item.version.category_key] += amount
        kind = classification(item.expense.payer_user_id, {s.user_id for s in item.shares})
        if kind == "personal":
            personal += amount
        else:
            shared += amount
    member_balances = balances(db, trip)
    return Dashboard(
        total=total,
        shared=shared,
        personal=personal,
        categories=dict(sorted(categories.items(), key=lambda kv: (-kv[1], kv[0]))),
        balances=member_balances,
        suggestions=allocation.suggest({u: b.net for u, b in member_balances.items()}),
        pending_count=int(
            db.scalar(
                select(func.count()).where(
                    TripExpenseVersion.trip_id == trip.id, TripExpenseVersion.state == "pending"
                )
            )
            or 0
        ),
    )
