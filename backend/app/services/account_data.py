"""One person's own data, as a single download (SECURITY.md §11.4).

Everything is scoped to the caller. Trip records are reduced to the caller's part of them: their
own membership, shares, payments and budget. No other member's name, email, ID, private budget or
linked transaction appears, and a note is included only if the caller wrote it.

Available during a deletion block and during the grace period, so asking to leave never costs
anyone access to their data.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Budget,
    Category,
    CategoryGroup,
    Debt,
    ExpenseShare,
    FinancialAccount,
    InstallmentItem,
    InstallmentPayment,
    InstallmentPlan,
    QuickTemplate,
    RecurringOccurrence,
    RecurringRule,
    SavingsGoal,
    SavingsGoalEvent,
    Settlement,
    Transaction,
    Trip,
    TripExpense,
    TripExpenseVersion,
    TripMember,
    TripPersonalBudget,
    User,
)
from app.services.trip_expenses import balances

FORMAT_NAME = "finpilot-personal-data"
FORMAT_VERSION = 1

# (section, model). Each model has a `user_id` column, which is what scopes it.
SECTIONS: tuple[tuple[str, Any], ...] = (
    ("accounts", FinancialAccount),
    ("transactions", Transaction),
    ("categories", Category),
    ("category_groups", CategoryGroup),
    ("budgets", Budget),
    ("savings_goals", SavingsGoal),
    ("savings_goal_events", SavingsGoalEvent),
    ("debts", Debt),
    ("installment_plans", InstallmentPlan),
    ("installment_items", InstallmentItem),
    ("installment_payments", InstallmentPayment),
    ("recurring_and_subscriptions", RecurringRule),
    ("recurring_occurrences", RecurringOccurrence),
    ("quick_templates", QuickTemplate),
)
_OMITTED_COLUMNS = {"user_id", "version"}
NOT_INCLUDED = (
    "journal_entries: the double-entry form of the transactions listed here",
    "other_trip_members: their names, shares and private budgets belong to them",
    "security_history: sign-in and session records",
)


def _plain(value: object) -> object:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _row(item: object) -> dict[str, object]:
    mapper = inspect(type(item))
    return {
        attr.key: _plain(getattr(item, attr.key))
        for attr in mapper.column_attrs
        if attr.key not in _OMITTED_COLUMNS
    }


def _capped(rows: Sequence[Any], limit: int, section: str, truncated: list[str]) -> Sequence[Any]:
    if len(rows) > limit:
        truncated.append(section)
        return rows[:limit]
    return rows


def _profile(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "locale": user.locale,
        "preferred_currency": user.preferred_currency,
        "time_zone": user.time_zone,
        "status": user.status,
        "created_at": _plain(user.created_at),
        "deletion_requested_at": _plain(user.deletion_requested_at),
        "equivalent_targets": list(user.equivalent_targets),
        "home_layout": user.home_layout,
    }


def _trip(
    db: Session, user: User, trip: Trip, member: TripMember, limit: int, truncated: list[str]
) -> dict[str, object]:
    section = f"trip:{trip.id}"
    shares = db.execute(
        select(ExpenseShare, TripExpenseVersion)
        .join(TripExpenseVersion, TripExpenseVersion.id == ExpenseShare.version_id)
        .where(ExpenseShare.user_id == user.id, ExpenseShare.trip_id == trip.id)
        .order_by(TripExpenseVersion.expense_date, TripExpenseVersion.id)
        .limit(limit + 1)
    ).all()
    paid = db.execute(
        select(TripExpense, TripExpenseVersion)
        .join(TripExpenseVersion, TripExpenseVersion.expense_id == TripExpense.id)
        .where(TripExpense.trip_id == trip.id, TripExpense.payer_user_id == user.id)
        .order_by(TripExpenseVersion.expense_date, TripExpenseVersion.id)
        .limit(limit + 1)
    ).all()
    payments = list(
        db.scalars(
            select(Settlement)
            .where(
                Settlement.trip_id == trip.id,
                or_(Settlement.payer_user_id == user.id, Settlement.recipient_user_id == user.id),
            )
            .order_by(Settlement.paid_on, Settlement.id)
            .limit(limit + 1)
        )
    )
    budget = db.get(TripPersonalBudget, (trip.id, user.id))
    mine = balances(db, trip).get(user.id)

    def own_note(version: TripExpenseVersion) -> str | None:
        return version.notes if version.proposed_by == user.id else None

    return {
        "name": trip.name,
        "description": trip.description,
        "destination": trip.destination,
        "start_date": _plain(trip.start_date),
        "end_date": _plain(trip.end_date),
        "status": trip.status,
        "base_currency": trip.base_currency,
        "my_membership": {
            "role": member.role,
            "status": member.status,
            "joined_at": _plain(member.joined_at),
            "ended_at": _plain(member.ended_at),
        },
        "my_balance_minor": (
            {
                "paid": mine.paid,
                "owed": mine.owed,
                "sent": mine.sent,
                "received": mine.received,
                "net": mine.net,
            }
            if mine
            else None
        ),
        "my_budget_minor": budget.amount_minor if budget else None,
        "my_shares": [
            {
                "date": _plain(version.expense_date),
                "description": version.description,
                "category": version.category_key,
                "state": version.state,
                "expense_amount_minor": version.amount_minor,
                "currency": version.currency,
                "my_share_minor": share.share_minor,
                "my_note": own_note(version),
            }
            for share, version in _capped(shares, limit, f"{section}:shares", truncated)
        ],
        "expenses_i_paid": [
            {
                "date": _plain(version.expense_date),
                "description": version.description,
                "category": version.category_key,
                "state": version.state,
                "amount_minor": version.amount_minor,
                "currency": version.currency,
                "my_note": own_note(version),
            }
            for _, version in _capped(paid, limit, f"{section}:paid", truncated)
        ],
        "my_payments": [
            {
                "direction": "sent" if row.payer_user_id == user.id else "received",
                "state": row.state,
                "paid_on": _plain(row.paid_on),
                "amount_minor": row.amount_minor,
                "currency": row.currency,
                "my_note": row.note if row.initiated_by == user.id else None,
            }
            for row in _capped(payments, limit, f"{section}:payments", truncated)
        ],
    }


def build(db: Session, user: User, *, limit: int) -> dict[str, object]:
    """Every section is capped, and anything cut is named, so a partial file can't pass as whole."""
    truncated: list[str] = []
    sections: dict[str, object] = {}
    for name, model in SECTIONS:
        rows: list[Any] = list(
            db.scalars(
                select(model).where(model.user_id == user.id).order_by(model.id).limit(limit + 1)
            )
        )
        sections[name] = [_row(row) for row in _capped(rows, limit, name, truncated)]
    memberships = db.execute(
        select(TripMember, Trip)
        .join(Trip, Trip.id == TripMember.trip_id)
        .where(TripMember.user_id == user.id)
        .order_by(TripMember.joined_at)
        .limit(limit + 1)
    ).all()
    trips = [
        _trip(db, user, trip, member, limit, truncated)
        for member, trip in _capped(memberships, limit, "trips", truncated)
    ]
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "complete": not truncated,
        "limits": {"rows_per_section": limit},
        "truncated_sections": truncated,
        "not_included": list(NOT_INCLUDED),
        "profile": _profile(user),
        **sections,
        "trips": trips,
    }


def filename(today: date) -> str:
    """Built only from a date, so nothing a person typed can reach the header."""
    return f"finpilot-data-{today.isoformat()}.json"
