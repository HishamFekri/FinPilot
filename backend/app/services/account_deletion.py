"""Asking to delete an account (SECURITY.md §11.3, Track 1).

Requesting only moves the account to `deletion_pending`, which can be undone by the person. Nothing
is erased here. Erasure is a separate maintenance step, see `account_erasure.py`.

The block on open trip balances is the recorded product behaviour (OD-3). Whether it should change
is an open legal and product decision (OD-18), so the rule lives here, in one function. Personal
debts and installments are only warned about: they're the person's own records, and other people
don't depend on them.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    Debt,
    ExpenseShare,
    InstallmentPlan,
    Trip,
    TripDispute,
    TripExpense,
    TripExpenseVersion,
    TripMember,
    User,
    UserSession,
)
from app.services import debts, sessions
from app.services.trip_expenses import balances, pending_involving

# Long enough to read the page and confirm; short enough that an unattended browser can't.
RECENT_SIGN_IN = timedelta(minutes=15)
PENDING = "deletion_pending"
_OPEN_TRIP_STATUSES = ("active", "completed")


@dataclass(frozen=True)
class Blocker:
    """Something that has to be resolved first. Names only the person's own trip."""

    kind: str  # trip_balance | trip_pending | trip_dispute | trip_owner
    trip_id: uuid.UUID
    trip_name: str
    amount_minor: int | None = None
    currency: str | None = None


@dataclass(frozen=True)
class Warning:
    """Doesn't stop deletion. The person is told, and has to acknowledge it."""

    kind: str  # personal_debts | installment_plans
    count: int


@dataclass(frozen=True)
class Eligibility:
    blockers: list[Blocker]
    warnings: list[Warning]


def eligibility(db: Session, user: User) -> Eligibility:
    return Eligibility(_blockers(db, user.id), _warnings(db, user.id))


def _blockers(db: Session, user_id: uuid.UUID) -> list[Blocker]:
    found: list[Blocker] = []
    memberships = db.execute(
        select(TripMember, Trip)
        .join(Trip, Trip.id == TripMember.trip_id)
        .where(TripMember.user_id == user_id)
        .order_by(TripMember.joined_at)
    ).all()
    for member, trip in memberships:
        mine = balances(db, trip).get(user_id)
        if mine is not None and mine.net != 0:
            found.append(Blocker("trip_balance", trip.id, trip.name, mine.net, trip.base_currency))
        if pending_involving(db, trip.id, user_id):
            found.append(Blocker("trip_pending", trip.id, trip.name))
        if _open_dispute(db, trip.id, user_id):
            found.append(Blocker("trip_dispute", trip.id, trip.name))
        if (
            member.role == "owner"
            and member.status == "active"
            and trip.status in _OPEN_TRIP_STATUSES
            and _others_active(db, trip.id, user_id)
        ):
            found.append(Blocker("trip_owner", trip.id, trip.name))
    return found


def _open_dispute(db: Session, trip_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    count = db.scalar(
        select(func.count())
        .select_from(TripDispute)
        .join(TripExpense, TripExpense.id == TripDispute.expense_id)
        .where(
            TripDispute.trip_id == trip_id,
            TripDispute.status == "open",
            or_(TripDispute.raised_by == user_id, TripExpense.payer_user_id == user_id),
        )
    )
    return bool(count)


def _others_active(db: Session, trip_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    count = db.scalar(
        select(func.count())
        .select_from(TripMember)
        .where(
            TripMember.trip_id == trip_id,
            TripMember.status == "active",
            TripMember.user_id != user_id,
        )
    )
    return bool(count)


def _warnings(db: Session, user_id: uuid.UUID) -> list[Warning]:
    out: list[Warning] = []
    open_debts = sum(
        1
        for debt in db.scalars(select(Debt).where(Debt.user_id == user_id, Debt.status == "active"))
        if debts.remaining(db, debt) > 0
    )
    if open_debts:
        out.append(Warning("personal_debts", open_debts))
    plans = db.scalar(
        select(func.count())
        .select_from(InstallmentPlan)
        .where(InstallmentPlan.user_id == user_id, InstallmentPlan.status == "active")
    )
    if plans:
        out.append(Warning("installment_plans", int(plans)))
    return out


def recently_signed_in(session: UserSession, now: datetime | None = None) -> bool:
    """A session is new only at a real Google sign-in: sign-in always issues a new one."""
    return (now or datetime.now(UTC)) - session.created_at <= RECENT_SIGN_IN


def request_deletion(
    db: Session, user: User, session: UserSession, *, confirm_email: str
) -> Eligibility:
    """Moves an active account to deletion_pending. Locked, so two requests can't both succeed."""
    db.refresh(user, with_for_update=True)
    if user.status == PENDING:
        raise ApiError(409, "deletion_already_requested", "Deletion was already requested")
    if user.status != "active":
        raise ApiError(403, "account_unavailable", "Account unavailable")
    # The address on the account, typed out: a misclick can't get past this.
    if confirm_email.strip().casefold() != (user.email or "").casefold():
        raise ApiError(422, "confirmation_mismatch", "That isn't this account's email address")
    if not recently_signed_in(session):
        raise ApiError(403, "recent_sign_in_required", "Sign in again to confirm it's you")
    result = eligibility(db, user)
    if result.blockers:
        raise ApiError(
            409,
            "deletion_blocked",
            "Some shared trip items have to be resolved first",
            {"blockers": [_blocker_out(b) for b in result.blockers]},
        )
    user.status = PENDING
    user.deletion_requested_at = datetime.now(UTC)
    # Any other browser is signed out. This one stays, so the person can change their mind.
    sessions.revoke_all(db, user.id, except_id=session.id, reason="deletion_requested")
    sessions.audit(db, "user.deletion.requested", user.id)
    db.flush()
    return result


def cancel_deletion(db: Session, user: User) -> None:
    db.refresh(user, with_for_update=True)
    if user.status != PENDING:
        raise ApiError(409, "deletion_not_requested", "There's no deletion to cancel")
    user.status = "active"
    user.deletion_requested_at = None
    sessions.audit(db, "user.deletion.cancelled", user.id)
    db.flush()


def _blocker_out(blocker: Blocker) -> dict[str, object]:
    return {
        "kind": blocker.kind,
        "trip_id": str(blocker.trip_id),
        "trip_name": blocker.trip_name,
        "amount_minor": blocker.amount_minor,
        "currency": blocker.currency,
    }


def share_count(db: Session, user_id: uuid.UUID) -> int:
    """How many shared expenses mention this person, for the explanation of what's kept."""
    return int(
        db.scalar(
            select(func.count(func.distinct(TripExpenseVersion.expense_id)))
            .select_from(ExpenseShare)
            .join(TripExpenseVersion, TripExpenseVersion.id == ExpenseShare.version_id)
            .where(ExpenseShare.user_id == user_id)
        )
        or 0
    )
