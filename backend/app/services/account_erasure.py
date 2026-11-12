"""The irreversible step of deleting an account (SECURITY.md §11.2). Maintenance only.

No route calls this. It runs from `scripts/erase_account.py`, as the schema owner, and that script
refuses every database except a *_test one until the retention decisions in §11.5 are made.

What happens, in one transaction:
- personal finance rows, sessions, the Google link and private audit events are deleted;
- the `users` row stays as a tombstone with no email, name or preferences;
- trip records stay, because other members' balances depend on them (`FINANCIAL_RULES.md` §9.2).
  The person appears under a neutral label, and what they wrote in notes is removed. That is
  pseudonymization, not anonymization: don't describe it to anyone as anonymous.

Append-only triggers refuse these deletes, as they should. They're lifted only inside this
transaction, which takes an exclusive lock, so no other session ever sees them off.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.hardening import PERSONAL
from app.db.models import (
    AuditEvent,
    FxRate,
    Settlement,
    TripDispute,
    TripExpenseVersion,
    TripMember,
    User,
    UserIdentity,
    UserSession,
)
from app.services import account_deletion, sessions

REMOVED = "[removed]"
_ALSO_GUARDED = (
    "audit_events",
    "fx_rates",
    "settlements",
    "trip_disputes",
    "trip_expense_versions",
)


class ErasureRefusedError(Exception):
    """Refused, with nothing changed. The message is a code, safe to print."""


def _set_guards(db: Session, tables: list[str], state: str) -> None:
    for table in tables:
        # Names come from constants in this codebase, never from input.
        db.execute(text(f"ALTER TABLE {table} {state} TRIGGER USER"))


def erase(db: Session, user_id: uuid.UUID, *, grace: timedelta) -> None:
    """Commits nothing itself, so the caller's transaction decides. Safe to refuse at any point."""
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise ErasureRefusedError("no_such_user")
    if user.status != account_deletion.PENDING or user.deletion_requested_at is None:
        raise ErasureRefusedError("not_pending_deletion")
    if datetime.now(UTC) - user.deletion_requested_at < grace:
        raise ErasureRefusedError("grace_period_not_over")
    # Checked again: other members can add shared expenses during the grace period.
    if account_deletion.eligibility(db, user).blockers:
        raise ErasureRefusedError("still_blocked")

    guarded = sorted({*PERSONAL, *_ALSO_GUARDED})
    _set_guards(db, guarded, "DISABLE")

    db.execute(update(User).where(User.id == user_id).values(default_account_id=None))
    # Reverse dependency order, so no foreign key blocks a delete.
    for table in reversed(Base.metadata.sorted_tables):
        column = PERSONAL.get(table.name)
        if column is not None:
            db.execute(delete(table).where(table.c[column] == user_id))

    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    db.execute(delete(UserIdentity).where(UserIdentity.user_id == user_id))
    db.execute(
        delete(AuditEvent).where(AuditEvent.scope == "user", AuditEvent.owner_user_id == user_id)
    )
    db.execute(update(FxRate).where(FxRate.entered_by == user_id).values(entered_by=None))

    # Shared records stay, without what this person wrote in them.
    db.execute(
        update(TripExpenseVersion)
        .where(TripExpenseVersion.proposed_by == user_id, TripExpenseVersion.notes.is_not(None))
        .values(notes=None)
    )
    db.execute(
        update(TripExpenseVersion)
        .where(
            TripExpenseVersion.decided_by == user_id,
            TripExpenseVersion.decision_reason.is_not(None),
        )
        .values(decision_reason=REMOVED)
    )
    db.execute(
        update(Settlement)
        .where(Settlement.initiated_by == user_id, Settlement.note.is_not(None))
        .values(note=None)
    )
    db.execute(
        update(Settlement)
        .where(Settlement.decided_by == user_id, Settlement.decision_reason.is_not(None))
        .values(decision_reason=REMOVED)
    )
    db.execute(update(TripDispute).where(TripDispute.raised_by == user_id).values(reason=REMOVED))
    db.execute(
        update(TripDispute)
        .where(TripDispute.resolved_by == user_id, TripDispute.resolution_note.is_not(None))
        .values(resolution_note=REMOVED)
    )
    db.execute(
        update(TripMember)
        .where(TripMember.user_id == user_id, TripMember.status == "active")
        .values(status="left", ended_at=func.now(), ended_by=user_id)
    )

    # version is bumped by hand: core updates skip the ORM's optimistic counter.
    db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            email=None,
            display_name=None,
            status="deleted",
            preferred_currency=None,
            time_zone="UTC",
            terms_version=None,
            terms_accepted_at=None,
            deletion_requested_at=None,
            last_active_at=None,
            setup_completed_at=None,
            home_layout=None,
            version=User.version + 1,
        )
    )
    # A system event: there's no longer a private history for it to belong to.
    sessions.audit(db, "user.erased", None, entity_type="user", entity_id=user_id)
    db.flush()
    _set_guards(db, guarded, "ENABLE")
    db.expire_all()
