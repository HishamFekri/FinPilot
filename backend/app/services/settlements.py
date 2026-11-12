"""Payments between trip members, as the members report them (FINANCIAL_RULES.md §13).

FinPilot moves no money: it records "Alice paid Bob €25" when Alice says so, and counts it only
once Bob confirms he received it. A suggested payment is never assumed to have happened.

- **Payment:** recorded by its payer; confirmed or rejected by its recipient; cancelled by its
  payer while pending. Any amount: a partial payment reduces the balance by exactly that much.
  Paying more than is owed is allowed with a warning (the balance then flips, §13.3).
- **Reversal:** a confirmed payment is never edited. Either party records a reversal (the same
  amount, the other way) and the other party confirms it. One live reversal per payment; a
  reversal can't be reversed (record a new payment instead). "Correct this payment" is a
  reversal plus a new payment that points at the old one (`corrects_settlement_id`).
- **Confirming twice** is harmless: the same person repeating the same decision gets the same
  answer; a different decision on a decided settlement is 409.
- Nobody confirms for someone else, not even the trip's owner. Former members can still record,
  confirm and reverse settlements involving them. Settlements work on active and completed
  trips, not archived ones.

B4 records settlements in the trip's base currency.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import Settlement, TripMember, User
from app.services import conversion, fx, money, trip_expenses
from app.services.text_safety import unsafe
from app.services.trips import Access, event, today_for

NOTE_MAX = 200
REASON_MAX = 500
LIVE = ("pending", "confirmed")


def _text(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > limit or unsafe(text):
        raise ApiError(422, "validation_error", "The note is too long or has hidden characters")
    return text


def _require_open_trip(a: Access) -> None:
    if a.trip.status == "archived":
        raise ApiError(409, "trip_not_active", "This trip is archived; nothing can change")


def _settlement(db: Session, a: Access, settlement_id: uuid.UUID) -> Settlement:
    row = db.scalar(
        select(Settlement)
        .where(Settlement.id == settlement_id, Settlement.trip_id == a.trip.id)
        .with_for_update()
    )
    if row is None or (
        not a.active and a.member.user_id not in (row.payer_user_id, row.recipient_user_id)
    ):
        raise ApiError(404, "not_found", "Payment not found")
    return row


@dataclass(frozen=True)
class PaymentRate:
    """How a payment in another currency counts in the trip's currency (§13.2). One of:
    a stored reference rate, a typed rate, or the trip-currency amount it settles."""

    rate_id: uuid.UUID | None = None
    manual_rate: str | None = None
    base_amount: str | None = None
    accept_stale: bool = False


def _in_base(
    db: Session,
    gateway: fx.FxGateway | None,
    user: User,
    trip_currency: str,
    minor: int,
    currency: str,
    day: date,
    rate: PaymentRate | None,
) -> tuple[int, uuid.UUID | None, list[str]]:
    """The trip-currency value of a payment, and the rate row it used (never invented)."""
    if currency == trip_currency:
        if rate and (rate.rate_id or rate.manual_rate or rate.base_amount):
            raise ApiError(
                422, "conversion_not_needed", "This payment is already in the trip's currency"
            )
        return minor, None, []
    choice = rate or PaymentRate()
    if gateway is None:
        raise ApiError(422, "rate_required", "Choose an exchange rate")
    common = {
        "original_minor": minor,
        "original_currency": currency,
        "account_currency": trip_currency,
        "on": day,
    }
    if choice.base_amount:
        done = conversion.apply(
            db, gateway, user, **common,  # type: ignore[arg-type]
            choice=conversion.Conversion(
                "actual_amount", rate_id=choice.rate_id, account_amount=choice.base_amount
            ),
        )  # fmt: skip
        # Both amounts were stated: their implied rate is kept as the person's own rate row.
        manual = fx.record_manual_rate(db, user, currency, trip_currency, done.rate, day)
        return done.posted_minor, manual.id, done.warnings
    if choice.manual_rate:
        done = conversion.apply(
            db, gateway, user, **common,  # type: ignore[arg-type]
            choice=conversion.Conversion("manual_rate", rate=choice.manual_rate),
        )  # fmt: skip
        return done.posted_minor, done.rate_id, done.warnings
    if choice.rate_id:
        done = conversion.apply(
            db, gateway, user, **common,  # type: ignore[arg-type]
            choice=conversion.Conversion(
                "reference", rate_id=choice.rate_id, accept_stale=choice.accept_stale
            ),
        )  # fmt: skip
        return done.posted_minor, done.rate_id, done.warnings
    raise ApiError(
        422,
        "rate_required",
        "Choose how this payment converts to the trip's currency",
        {"from": currency, "to": trip_currency},
    )


def record(
    db: Session,
    user: User,
    a: Access,
    *,
    recipient_id: uuid.UUID,
    amount: str,
    currency: str,
    paid_on: date | None,
    note: str | None,
    corrects: uuid.UUID | None = None,
    confirm_duplicate: bool = False,
    rate: PaymentRate | None = None,
    gateway: fx.FxGateway | None = None,
) -> tuple[Settlement, list[str]]:
    """ "I paid {recipient} {amount}." The caller is always the payer.

    In another currency, the payment keeps what was actually paid and its trip-currency value
    at the chosen rate, fixed for good: balances stay in the trip's currency and a later rate
    never changes a recorded payment."""
    _require_open_trip(a)
    trip = a.trip
    if recipient_id == user.id:
        raise ApiError(422, "validation_error", "Choose someone else on the trip")
    if db.get(TripMember, (trip.id, recipient_id)) is None:
        raise ApiError(422, "recipient_not_member", "The recipient isn't on this trip")
    minor, _ = money.parse(db, amount, currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    day = paid_on or today_for(user)
    if day > today_for(user) + timedelta(days=1):
        raise ApiError(422, "date_in_future", "A payment can't be dated in the future")
    base_minor, rate_id, rate_warnings = _in_base(
        db, gateway, user, trip.base_currency, minor, currency, day, rate
    )

    if corrects is not None:
        old = db.get(Settlement, corrects)
        live_reversal = db.scalar(
            select(Settlement.id).where(
                Settlement.reverses_settlement_id == corrects, Settlement.state.in_(LIVE)
            )
        )
        if (
            old is None
            or old.trip_id != trip.id
            or old.kind != "payment"
            or {old.payer_user_id, old.recipient_user_id} != {user.id, recipient_id}
            or live_reversal is None
        ):
            raise ApiError(
                422,
                "validation_error",
                "A correction replaces a reversed payment between the same two people",
            )

    if not confirm_duplicate:
        duplicate = db.scalar(
            select(Settlement.id).where(
                Settlement.trip_id == trip.id,
                Settlement.kind == "payment",
                Settlement.payer_user_id == user.id,
                Settlement.recipient_user_id == recipient_id,
                Settlement.amount_minor == minor,
                Settlement.currency == currency,
                Settlement.paid_on == day,
                Settlement.state.in_(LIVE),
            )
        )
        if duplicate is not None:
            raise ApiError(
                409,
                "possible_duplicate",
                "A payment of this amount to this person on this day is already recorded",
                {"settlement_id": str(duplicate)},
            )

    warnings: list[str] = list(rate_warnings)
    nets = trip_expenses.nets(db, trip)
    if base_minor > -nets.get(user.id, 0) or base_minor > nets.get(recipient_id, 0):
        warnings.append("exceeds_outstanding_balance")

    row = Settlement(
        trip_id=trip.id,
        payer_user_id=user.id,
        recipient_user_id=recipient_id,
        kind="payment",
        corrects_settlement_id=corrects,
        initiated_by=user.id,
        amount_minor=minor,
        currency=currency,
        base_currency=trip.base_currency,
        base_amount_minor=base_minor,
        rate_id=rate_id,
        paid_on=day,
        note=_text(note, NOTE_MAX),
    )
    db.add(row)
    db.flush()
    event(db, trip.id, user.id, "settlement.recorded", entity_type="settlement", entity_id=row.id)
    return row, warnings


def decide(
    db: Session,
    user: User,
    a: Access,
    settlement_id: uuid.UUID,
    action: str,
    reason: str | None = None,
) -> Settlement:
    """confirm or reject (the party who didn't record it), cancel (the one who did)."""
    _require_open_trip(a)
    row = _settlement(db, a, settlement_id)
    target = {"confirm": "confirmed", "reject": "rejected", "cancel": "cancelled"}[action]
    parties = (row.payer_user_id, row.recipient_user_id)
    counterparty = (
        row.recipient_user_id if row.initiated_by == row.payer_user_id else row.payer_user_id
    )
    if action in ("confirm", "reject") and user.id != counterparty:
        raise ApiError(
            403,
            "not_counterparty",
            "Only the other person in this payment can confirm or reject it",
        )
    if action == "cancel" and user.id != row.initiated_by:
        raise ApiError(403, "forbidden", "Only the person who recorded it can cancel it")
    if user.id not in parties:  # pragma: no cover - covered by the checks above
        raise ApiError(403, "forbidden", "Not a party to this payment")
    if row.state != "pending":
        if row.state == target and row.decided_by == user.id:
            return row  # the same decision again: an idempotent success
        raise ApiError(
            409, "settlement_not_pending", "This payment was already decided", {"state": row.state}
        )
    row.state = target
    row.decided_by = user.id
    row.decided_at = datetime.now(UTC)
    row.decision_reason = _text(reason, REASON_MAX) if action == "reject" else None
    db.flush()
    kind = "reversal" if row.kind == "reversal" else "settlement"
    event(db, a.trip.id, user.id, f"{kind}.{target}", entity_type="settlement", entity_id=row.id)
    return row


def reverse(db: Session, user: User, a: Access, settlement_id: uuid.UUID) -> Settlement:
    """Start undoing a confirmed payment. It counts only once the other person confirms."""
    _require_open_trip(a)
    original = _settlement(db, a, settlement_id)
    if user.id not in (original.payer_user_id, original.recipient_user_id):
        raise ApiError(403, "forbidden", "Only the two people in this payment can reverse it")
    if original.kind != "payment":
        raise ApiError(409, "invalid_state", "A reversal can't be reversed; record a new payment")
    if original.state != "confirmed":
        raise ApiError(409, "invalid_state", "Only a confirmed payment can be reversed")
    live = db.scalar(
        select(Settlement.id).where(
            Settlement.reverses_settlement_id == original.id, Settlement.state.in_(LIVE)
        )
    )
    if live is not None:
        raise ApiError(
            409,
            "reversal_exists",
            "A reversal of this payment already exists",
            {"settlement_id": str(live)},
        )
    row = Settlement(
        trip_id=a.trip.id,
        payer_user_id=original.recipient_user_id,
        recipient_user_id=original.payer_user_id,
        kind="reversal",
        reverses_settlement_id=original.id,
        initiated_by=user.id,
        amount_minor=original.amount_minor,
        currency=original.currency,
        base_currency=original.base_currency,
        base_amount_minor=original.base_amount_minor,
        rate_id=original.rate_id,  # the same values: a reversal undoes exactly what counted
        paid_on=today_for(user),
    )
    db.add(row)
    db.flush()
    event(db, a.trip.id, user.id, "reversal.recorded", entity_type="settlement", entity_id=row.id)
    return row


# --- Reading


@dataclass(frozen=True)
class SettlementView:
    row: Settlement
    reversal: Settlement | None  # the live or latest reversal of a payment

    @property
    def status(self) -> str:
        """Shown status: pending, confirmed, rejected, cancelled, reversal_pending or reversed."""
        if self.row.kind == "payment" and self.row.state == "confirmed" and self.reversal:
            if self.reversal.state == "confirmed":
                return "reversed"
            if self.reversal.state == "pending":
                return "reversal_pending"
        return self.row.state


def views(db: Session, a: Access) -> list[SettlementView]:
    """Newest first. A former member sees only payments they're part of."""
    rows = list(
        db.scalars(
            select(Settlement)
            .where(Settlement.trip_id == a.trip.id)
            .order_by(Settlement.created_at.desc(), Settlement.id)
        )
    )
    reversals: dict[uuid.UUID, Settlement] = {}
    for r in sorted(rows, key=lambda r: r.created_at):  # the latest reversal wins
        if r.reverses_settlement_id is not None:
            current = reversals.get(r.reverses_settlement_id)
            if current is None or r.state in LIVE or current.state not in LIVE:
                reversals[r.reverses_settlement_id] = r
    out = [SettlementView(r, reversals.get(r.id)) for r in rows]
    if not a.active:
        me = a.member.user_id
        out = [v for v in out if me in (v.row.payer_user_id, v.row.recipient_user_id)]
    return out


def waiting_on(db: Session, trip_id: uuid.UUID, user_id: uuid.UUID) -> list[Settlement]:
    """Pending settlements this person has to confirm or reject."""
    rows = db.scalars(
        select(Settlement)
        .where(Settlement.trip_id == trip_id, Settlement.state == "pending")
        .order_by(Settlement.created_at)
    )
    return [
        r
        for r in rows
        if user_id
        == (r.recipient_user_id if r.initiated_by == r.payer_user_id else r.payer_user_id)
    ]


def waiting_counts(db: Session, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Per trip, settlements waiting on this person (for the trip list)."""
    rows = db.scalars(
        select(Settlement).where(
            Settlement.state == "pending",
            (Settlement.payer_user_id == user_id) | (Settlement.recipient_user_id == user_id),
            Settlement.initiated_by != user_id,
        )
    )
    out: dict[uuid.UUID, int] = {}
    for r in rows:
        out[r.trip_id] = out.get(r.trip_id, 0) + 1
    return out
