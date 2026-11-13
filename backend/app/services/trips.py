"""Trips, membership, roles, and invitations (PRD.md §4.1-4.2, SECURITY.md §4-5).

**Privacy boundary.** This module and the other trip modules never import personal-finance
modules (accounts, ledger, transactions, budgets, reporting): a trip can't read anyone's private
accounts. Members are exposed only as id, display name, role and status (SECURITY.md §4.5); never
an email address.

**Not found, not forbidden.** Someone who isn't, and never was, a member gets 404 for everything
about a trip, so trip ids can't be probed. Former members keep a scoped view (§4.4).

Every change writes a trip-scoped audit event: that is the trip's activity feed. Event metadata
holds codes and ids only, never free text.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import ApiError
from app.core.logging import request_id_var
from app.db import rls
from app.db.models import (
    AuditEvent,
    Trip,
    TripExpense,
    TripExpenseVersion,
    TripInvitation,
    TripMember,
    User,
)
from app.services import money
from app.services.text_safety import unsafe

INVITE_DAYS = 7
NAME_MAX = 100


def today_for(user: User) -> date:
    try:
        return clock.today(ZoneInfo(user.time_zone))
    except (KeyError, ValueError):
        return clock.today(UTC)


def event(
    db: Session,
    trip_id: uuid.UUID,
    actor: uuid.UUID | None,
    action: str,
    *,
    entity_type: str = "trip",
    entity_id: uuid.UUID | None = None,
    **metadata: str,
) -> None:
    """A trip activity entry, in the caller's transaction."""
    db.add(
        AuditEvent(
            action=action,
            scope="trip",
            trip_id=trip_id,
            owner_user_id=None,
            actor_user_id=actor,
            entity_type=entity_type,
            entity_id=entity_id or trip_id,
            event_metadata=metadata,
            request_id=request_id_var.get(),
        )
    )


# --- Access


@dataclass(frozen=True)
class Access:
    trip: Trip
    member: TripMember

    @property
    def active(self) -> bool:
        return self.member.status == "active"

    @property
    def admin(self) -> bool:
        return self.active and self.member.role in ("owner", "admin")

    @property
    def owner(self) -> bool:
        return self.active and self.member.role == "owner"


def access(db: Session, user: User, trip_id: uuid.UUID, *, lock: bool = False) -> Access:
    """The caller's membership, current or former; 404 if they never belonged."""
    query = select(Trip).where(Trip.id == trip_id)
    if lock:
        query = query.with_for_update()
    trip = db.scalar(query)
    member = db.get(TripMember, (trip_id, user.id)) if trip else None
    if trip is None or member is None:
        raise ApiError(404, "not_found", "Trip not found")
    return Access(trip, member)


def require_active(a: Access) -> None:
    if not a.active:
        raise ApiError(403, "forbidden", "Only current members can do this")


def require_admin(a: Access) -> None:
    if not a.admin:
        raise ApiError(403, "forbidden", "Only the trip's owner or an admin can do this")


def require_owner(a: Access) -> None:
    if not a.owner:
        raise ApiError(403, "forbidden", "Only the trip's owner can do this")


def require_status(trip: Trip, *allowed: str) -> None:
    if trip.status not in allowed:
        raise ApiError(
            409,
            "trip_not_active" if "active" in allowed else "invalid_state",
            "The trip isn't active. Reopen it first" if "active" in allowed else "Not allowed now",
            {"status": trip.status},
        )


def check_version(trip: Trip, version: int | None) -> None:
    if version is not None and version != trip.version:
        raise ApiError(409, "version_conflict", "The trip changed; reload and try again")


# --- Trips


def _clean(value: str | None, field: str, limit: int, *, required: bool = False) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise ApiError(422, "validation_error", f"The {field} is required")
        return None
    if len(text) > limit or unsafe(text):
        raise ApiError(422, "validation_error", f"The {field} is too long or has hidden characters")
    return text


def _dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise ApiError(422, "validation_error", "The trip ends before it starts")


def list_for(db: Session, user: User, status: str | None = None) -> list[tuple[Trip, TripMember]]:
    query = (
        select(Trip, TripMember)
        .join(TripMember, TripMember.trip_id == Trip.id)
        .where(TripMember.user_id == user.id)
        .order_by(
            Trip.status != "active", Trip.start_date.desc().nulls_last(), Trip.created_at.desc()
        )
    )
    if status:
        query = query.where(Trip.status == status)
    return [(t, m) for t, m in db.execute(query)]


def create(
    db: Session,
    user: User,
    *,
    name: str,
    base_currency: str,
    description: str | None,
    destination: str | None,
    start_date: date | None,
    end_date: date | None,
) -> Trip:
    money.currency(db, base_currency)
    _dates(start_date, end_date)
    trip = Trip(
        name=_clean(name, "name", NAME_MAX, required=True),
        description=_clean(description, "description", 500),
        destination=_clean(destination, "destination", 100),
        start_date=start_date,
        end_date=end_date,
        base_currency=base_currency,
        created_by=user.id,
    )
    db.add(trip)
    db.flush()
    db.add(TripMember(trip_id=trip.id, user_id=user.id, role="owner"))
    event(db, trip.id, user.id, "trip.created")
    db.flush()
    db.refresh(trip)
    return trip


def has_expenses(db: Session, trip_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count()).where(TripExpense.trip_id == trip_id)) != 0


def phase(trip: Trip, today: date) -> str:
    """What the trip is now, for display: its stored status, or for an active trip its dates.

    upcoming (starts later), ongoing (today is within it, or it has no dates), ended (active, but
    past its end date and not closed yet), completed, cancelled, archived.
    """
    if trip.status != "active":
        return trip.status
    if trip.start_date is not None and trip.start_date > today:
        return "upcoming"
    if trip.end_date is not None and trip.end_date < today:
        return "ended"
    return "ongoing"


def update(
    db: Session, a: Access, user: User, version: int | None, changes: dict[str, object]
) -> Trip:
    require_admin(a)
    check_version(a.trip, version)
    trip = a.trip
    if "base_currency" in changes and changes["base_currency"] != trip.base_currency:
        if has_expenses(db, trip.id):
            raise ApiError(
                409, "base_currency_locked", "The base currency is fixed once a trip has expenses"
            )
        if trip.shared_budget_minor is not None and "shared_budget" not in changes:
            raise ApiError(
                409,
                "shared_budget_currency",
                "The shared budget is in the old currency: enter it again with the new one",
            )
        money.currency(db, str(changes["base_currency"]))
        trip.base_currency = str(changes["base_currency"])
    if "shared_budget" in changes:
        value = changes["shared_budget"]
        if value is None or not str(value).strip():
            trip.shared_budget_minor = None
        else:
            minor, _ = money.parse(db, str(value), trip.base_currency)
            if minor <= 0:
                raise ApiError(422, "amount_not_positive", "The budget must be greater than zero")
            trip.shared_budget_minor = minor
    if "name" in changes:
        trip.name = _clean(str(changes["name"] or ""), "name", NAME_MAX, required=True) or trip.name
    for field, limit in (("description", 500), ("destination", 100)):
        if field in changes:
            value = changes[field]
            setattr(trip, field, _clean(None if value is None else str(value), field, limit))
    if "start_date" in changes:
        trip.start_date = changes["start_date"]  # type: ignore[assignment]
    if "end_date" in changes:
        trip.end_date = changes["end_date"]  # type: ignore[assignment]
    _dates(trip.start_date, trip.end_date)
    trip.version += 1
    event(db, trip.id, user.id, "trip.updated")
    db.flush()
    return trip


def pending_versions(db: Session, trip_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count()).where(
                TripExpenseVersion.trip_id == trip_id, TripExpenseVersion.state == "pending"
            )
        )
        or 0
    )


def set_status(
    db: Session, a: Access, user: User, action: str, *, acknowledge: bool = False
) -> Trip:
    """complete, reopen, cancel, reinstate (admin); archive, unarchive (owner).

    Cancelling keeps everything: expenses, payments and balances stay, payments can still be
    settled, and no new expense can be added (like a completed trip). Reinstating undoes it.
    """
    trip = a.trip
    if action in ("complete", "reopen", "cancel", "reinstate"):
        require_admin(a)
    else:
        require_owner(a)
    moves = {
        "complete": (("active",), "completed"),
        "reopen": (("completed",), "active"),
        "cancel": (("active",), "cancelled"),
        "reinstate": (("cancelled",), "active"),
        "archive": (("completed", "cancelled"), "archived"),
        "unarchive": (("archived",), "cancelled" if trip.cancelled_at else "completed"),
    }
    before, after = moves[action]
    if trip.status not in before:
        raise ApiError(409, "invalid_state", "The trip can't do that from its current status")
    if action in ("complete", "cancel") and pending_versions(db, trip.id):
        raise ApiError(
            409, "pending_items_exist", "Resolve the expenses waiting for confirmation first"
        )
    if action == "archive":
        from app.services import trip_expenses  # trip modules only

        if trip_expenses.pending_settlements(db, trip.id):
            raise ApiError(
                409, "pending_items_exist", "Some payments are still waiting for confirmation"
            )
    if action == "archive" and not acknowledge:
        from app.services import trip_expenses  # trip modules only

        if any(net != 0 for net in trip_expenses.nets(db, trip).values()):
            raise ApiError(
                409,
                "balances_outstanding",
                "Some balances aren't settled. Confirm to archive anyway",
            )
    trip.status = after
    if action == "cancel":
        trip.cancelled_at = datetime.now(UTC)
    elif action in ("reinstate", "reopen"):
        trip.cancelled_at = None
    trip.version += 1
    past = {"cancel": "cancelled", "reinstate": "reinstated", "complete": "completed",
            "reopen": "reopened", "archive": "archived", "unarchive": "unarchived"}  # fmt: skip
    event(db, trip.id, user.id, f"trip.{past[action]}")
    db.flush()
    return trip


# --- Members


@dataclass(frozen=True)
class MemberView:
    user_id: uuid.UUID
    display_name: str
    role: str
    status: str


def members(db: Session, trip_id: uuid.UUID) -> list[MemberView]:
    rows = db.execute(
        select(TripMember, User.display_name, User.status)
        .join(User, User.id == TripMember.user_id)
        .where(TripMember.trip_id == trip_id)
        .order_by(TripMember.status != "active", TripMember.joined_at)
    )
    return [
        MemberView(
            m.user_id,
            name if status != "deleted" and name else "",
            m.role,
            m.status,
        )
        for m, name, status in rows
    ]


def active_member(db: Session, trip_id: uuid.UUID, user_id: uuid.UUID) -> TripMember | None:
    member = db.get(TripMember, (trip_id, user_id))
    return member if member and member.status == "active" else None


def change_role(db: Session, a: Access, user: User, target: uuid.UUID, role: str) -> None:
    require_owner(a)
    if role not in ("admin", "member"):
        raise ApiError(422, "validation_error", "Use transfer-ownership to change the owner")
    member = active_member(db, a.trip.id, target)
    if member is None or member.role == "owner":
        raise ApiError(404, "not_found", "No such active member")
    member.role = role
    event(
        db,
        a.trip.id,
        user.id,
        "trip.member.role_changed",
        entity_type="trip_member",
        entity_id=target,
        role=role,
    )
    db.flush()


def transfer_ownership(db: Session, a: Access, user: User, target: uuid.UUID) -> None:
    require_owner(a)
    member = active_member(db, a.trip.id, target)
    if member is None or target == user.id:
        raise ApiError(422, "validation_error", "Choose another current member")
    a.member.role = "admin"
    db.flush()  # one active owner at a time (partial unique index)
    member.role = "owner"
    event(
        db,
        a.trip.id,
        user.id,
        "trip.ownership_transferred",
        entity_type="trip_member",
        entity_id=target,
    )
    db.flush()


def _end(db: Session, member: TripMember, by: uuid.UUID, status: str) -> None:
    member.status = status
    member.ended_at = datetime.now(UTC)
    member.ended_by = by


def leave(db: Session, a: Access, user: User) -> None:
    from app.services import trip_expenses

    require_active(a)
    if a.member.role == "owner":
        raise ApiError(409, "owner_must_transfer", "Transfer ownership before leaving")
    if trip_expenses.nets(db, a.trip).get(user.id, 0) != 0:
        raise ApiError(409, "balance_outstanding", "Settle your balance before leaving")
    if trip_expenses.pending_involving(db, a.trip.id, user.id):
        raise ApiError(409, "pending_items_exist", "Some expenses involving you are still waiting")
    _end(db, a.member, user.id, "left")
    event(db, a.trip.id, user.id, "trip.member.left", entity_type="trip_member", entity_id=user.id)
    db.flush()


def remove(db: Session, a: Access, user: User, target: uuid.UUID) -> None:
    require_admin(a)
    member = active_member(db, a.trip.id, target)
    if member is None:
        raise ApiError(404, "not_found", "No such active member")
    if member.role == "owner":
        raise ApiError(409, "invalid_state", "The owner can't be removed")
    if member.role == "admin" and not a.owner:
        raise ApiError(403, "forbidden", "Only the owner can remove an admin")
    _end(db, member, user.id, "removed")
    event(
        db, a.trip.id, user.id, "trip.member.removed", entity_type="trip_member", entity_id=target
    )
    db.flush()


# --- Invitations (single-use links; the token is shown once and stored only as a hash)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def invite(db: Session, a: Access, user: User, label: str | None) -> tuple[TripInvitation, str]:
    require_admin(a)
    require_status(a.trip, "active")
    token = secrets.token_urlsafe(32)
    row = TripInvitation(
        trip_id=a.trip.id,
        token_hash=_hash(token),
        invited_by=user.id,
        invitee_label=_clean(label, "label", 60),
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_DAYS),
    )
    db.add(row)
    db.flush()
    event(
        db,
        a.trip.id,
        user.id,
        "trip.invitation.created",
        entity_type="trip_invitation",
        entity_id=row.id,
    )
    return row, token


def invitations(db: Session, a: Access) -> list[TripInvitation]:
    require_admin(a)
    return list(
        db.scalars(
            select(TripInvitation)
            .where(TripInvitation.trip_id == a.trip.id)
            .order_by(TripInvitation.created_at.desc())
        )
    )


def revoke(db: Session, a: Access, user: User, invitation_id: uuid.UUID) -> None:
    require_admin(a)
    row = db.get(TripInvitation, invitation_id)
    if row is None or row.trip_id != a.trip.id:
        raise ApiError(404, "not_found", "No such invitation")
    if row.status != "pending":
        raise ApiError(409, "invitation_not_pending", "This invitation was already used or revoked")
    row.status = "revoked"
    row.decided_by = user.id
    row.decided_at = datetime.now(UTC)
    event(
        db,
        a.trip.id,
        user.id,
        "trip.invitation.revoked",
        entity_type="trip_invitation",
        entity_id=row.id,
    )
    db.flush()


def _usable(db: Session, token: str, *, lock: bool = False) -> TripInvitation:
    """The pending, unexpired invitation for a token, or a clear refusal."""
    if not token or len(token) > 200:
        raise ApiError(404, "invitation_invalid", "This invitation link isn't valid")
    rls.holding_invitation(db, _hash(token))
    query = select(TripInvitation).where(TripInvitation.token_hash == _hash(token))
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise ApiError(404, "invitation_invalid", "This invitation link isn't valid")
    if row.status != "pending":
        raise ApiError(410, "invitation_used", "This invitation was already used or revoked")
    if row.expires_at <= datetime.now(UTC):
        raise ApiError(410, "invitation_expired", "This invitation has expired")
    return row


@dataclass(frozen=True)
class InvitePreview:
    trip: Trip
    inviter: str
    already_member: bool


def preview(db: Session, user: User, token: str) -> InvitePreview:
    row = _usable(db, token)
    trip = db.get(Trip, row.trip_id)
    inviter = db.get(User, row.invited_by)
    if trip is None:  # pragma: no cover - FK
        raise ApiError(404, "invitation_invalid", "This invitation link isn't valid")
    return InvitePreview(
        trip,
        (inviter.display_name or "") if inviter else "",
        active_member(db, trip.id, user.id) is not None,
    )


def accept(db: Session, user: User, token: str) -> Trip:
    row = _usable(db, token, lock=True)
    trip = db.scalar(select(Trip).where(Trip.id == row.trip_id).with_for_update())
    if trip is None:  # pragma: no cover - FK
        raise ApiError(404, "invitation_invalid", "This invitation link isn't valid")
    if trip.status != "active":
        raise ApiError(409, "trip_not_active", "This trip isn't taking new members")
    member = db.get(TripMember, (trip.id, user.id))
    if member is not None and member.status == "active":
        raise ApiError(409, "already_member", "You're already on this trip")
    if member is None:
        db.add(TripMember(trip_id=trip.id, user_id=user.id, role="member"))
        action = "trip.member.joined"
    else:  # rejoining reactivates the same membership and history
        member.status = "active"
        member.role = "member"
        member.ended_at = None
        member.ended_by = None
        action = "trip.member.rejoined"
    row.status = "accepted"
    row.decided_by = user.id
    row.decided_at = datetime.now(UTC)
    event(db, trip.id, user.id, action, entity_type="trip_member", entity_id=user.id)
    db.flush()
    return trip


def decline(db: Session, user: User, token: str) -> None:
    row = _usable(db, token, lock=True)
    row.status = "declined"
    row.decided_by = user.id
    row.decided_at = datetime.now(UTC)
    db.flush()


# --- Activity


def activity(db: Session, trip_id: uuid.UUID, limit: int = 30) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.scope == "trip", AuditEvent.trip_id == trip_id)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
            .limit(limit)
        )
    )
