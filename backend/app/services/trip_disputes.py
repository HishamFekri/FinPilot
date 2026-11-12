"""Disputes on approved trip expenses (FINANCIAL_RULES.md §12.8, SECURITY.md §4.3).

A dispute is a flag, never a state: it changes no balance, no share, and not the expense's status.
It protects the people assigned a cost the way D10 protects the payer.

- **Raise:** a beneficiary of the current approved version, including one who has left; never the
  payer (they can correct it instead). One open dispute per person per version. A reason is
  required (up to 500 characters).
- **Withdraw:** the person who raised it, any time.
- **Resolve** (upheld or dismissed, with a required note): the owner or an admin who has no
  interest in it, so not the payer, not whoever proposed that version, not the raiser. "Upheld"
  is a record only; changing the money still takes a correction the payer confirms.
- **Closed automatically** (`closed_superseded`) when a newer version or a cancellation is
  approved (see `trip_expenses._supersede_current`).
- With no neutral resolver (for example two people, the owner paid) it stays open. It blocks
  nothing; settlements stay available.

In-app notice: open disputes appear on the expense, in the trip's attention panel for the people
who can resolve them and for the payer, and in the activity feed.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import ExpenseShare, TripDispute, TripExpense, TripExpenseVersion, User
from app.services.text_safety import unsafe
from app.services.trips import Access, event

REASON_MAX = 500


def _text(value: str | None, name: str, *, required: bool) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise ApiError(422, "validation_error", f"A {name} is required")
        return None
    if len(text) > REASON_MAX or unsafe(text, allow_newline=True):
        raise ApiError(422, "validation_error", f"The {name} is too long or has hidden characters")
    return text


def _require_open_trip(a: Access) -> None:
    if a.trip.status == "archived":
        raise ApiError(409, "trip_not_active", "This trip is archived; nothing can change")


def raise_dispute(
    db: Session, user: User, a: Access, expense_id: uuid.UUID, version_id: uuid.UUID, reason: str
) -> TripDispute:
    _require_open_trip(a)
    expense = db.scalar(
        select(TripExpense)
        .where(TripExpense.id == expense_id, TripExpense.trip_id == a.trip.id)
        .with_for_update()  # serialises with a correction being approved (§14)
    )
    if expense is None:
        raise ApiError(404, "not_found", "Expense not found")
    current = db.scalar(
        select(TripExpenseVersion).where(
            TripExpenseVersion.expense_id == expense.id,
            TripExpenseVersion.state == "approved",
            TripExpenseVersion.kind != "cancellation",
        )
    )
    if current is None or current.id != version_id:
        raise ApiError(
            409, "version_not_current", "That version no longer counts; look at the current one"
        )
    beneficiary = db.get(ExpenseShare, (current.id, user.id)) is not None
    if not beneficiary:
        # Not part of it: as good as not visible to a former member.
        raise ApiError(
            403, "not_beneficiary", "Only someone who shares this expense can dispute it"
        )
    if user.id == expense.payer_user_id:
        raise ApiError(403, "payer_cannot_dispute", "As the payer, correct the expense instead")
    existing = db.scalar(
        select(TripDispute.id).where(
            TripDispute.version_id == current.id,
            TripDispute.raised_by == user.id,
            TripDispute.status == "open",
        )
    )
    if existing is not None:
        raise ApiError(409, "dispute_already_open", "You already have an open dispute on this")
    dispute = TripDispute(
        trip_id=a.trip.id,
        expense_id=expense.id,
        version_id=current.id,
        raised_by=user.id,
        reason=_text(reason, "reason", required=True) or "",
    )
    db.add(dispute)
    db.flush()
    event(
        db, a.trip.id, user.id, "dispute.raised", entity_type="trip_expense", entity_id=expense.id
    )
    return dispute


def _dispute(db: Session, a: Access, dispute_id: uuid.UUID) -> TripDispute:
    row = db.scalar(
        select(TripDispute)
        .where(TripDispute.id == dispute_id, TripDispute.trip_id == a.trip.id)
        .with_for_update()
    )
    if row is None or (not a.active and row.raised_by != a.member.user_id):
        raise ApiError(404, "not_found", "Dispute not found")
    return row


def withdraw(db: Session, user: User, a: Access, dispute_id: uuid.UUID) -> TripDispute:
    _require_open_trip(a)
    row = _dispute(db, a, dispute_id)
    if row.raised_by != user.id:
        raise ApiError(403, "forbidden", "Only the person who raised it can withdraw it")
    if row.status != "open":
        if row.status == "withdrawn":
            return row
        raise ApiError(409, "dispute_not_open", "This dispute is already closed")
    row.status = "withdrawn"
    row.decided_at = datetime.now(UTC)
    db.flush()
    event(
        db,
        a.trip.id,
        user.id,
        "dispute.withdrawn",
        entity_type="trip_expense",
        entity_id=row.expense_id,
    )
    return row


def is_payer(db: Session, row: TripDispute, user_id: uuid.UUID) -> bool:
    expense = db.get(TripExpense, row.expense_id)
    return expense is not None and expense.payer_user_id == user_id


def interested(db: Session, row: TripDispute, user_id: uuid.UUID) -> bool:
    """The payer, the proposer of the disputed version, or the raiser."""
    expense = db.get(TripExpense, row.expense_id)
    version = db.get(TripExpenseVersion, row.version_id)
    return user_id in {
        row.raised_by,
        expense.payer_user_id if expense else None,
        version.proposed_by if version else None,
    }


def resolve(
    db: Session, user: User, a: Access, dispute_id: uuid.UUID, outcome: str, note: str
) -> TripDispute:
    _require_open_trip(a)
    row = _dispute(db, a, dispute_id)
    if not a.admin:
        raise ApiError(403, "forbidden", "Only the trip's owner or an admin can resolve it")
    if interested(db, row, user.id):
        raise ApiError(
            403, "interested_party", "Someone involved in this expense can't resolve its dispute"
        )
    if outcome not in ("upheld", "dismissed"):
        raise ApiError(422, "validation_error", "Choose upheld or dismissed")
    if row.status != "open":
        raise ApiError(409, "dispute_not_open", "This dispute is already closed")
    row.status = outcome
    row.resolution_note = _text(note, "note", required=True)
    row.resolved_by = user.id
    row.decided_at = datetime.now(UTC)
    db.flush()
    event(
        db,
        a.trip.id,
        user.id,
        f"dispute.{outcome}",
        entity_type="trip_expense",
        entity_id=row.expense_id,
    )
    return row


def for_trip(db: Session, a: Access, *, status: str | None = None) -> list[TripDispute]:
    """Newest first. A former member sees only disputes on expenses they're part of."""
    query = select(TripDispute).where(TripDispute.trip_id == a.trip.id)
    if status:
        query = query.where(TripDispute.status == status)
    rows = list(db.scalars(query.order_by(TripDispute.created_at.desc())))
    if not a.active:
        from app.services import trip_expenses

        visible = {v.expense.id for v in trip_expenses.list_views(db, a)}
        rows = [r for r in rows if r.expense_id in visible]
    return rows


def open_by_expense(db: Session, trip_id: uuid.UUID) -> dict[uuid.UUID, list[TripDispute]]:
    out: dict[uuid.UUID, list[TripDispute]] = {}
    for row in db.scalars(
        select(TripDispute).where(TripDispute.trip_id == trip_id, TripDispute.status == "open")
    ):
        out.setdefault(row.expense_id, []).append(row)
    return out
