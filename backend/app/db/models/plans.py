"""Installment plans and trip planning (migration 0016).

Installments (FINANCIAL_RULES.md §4.13): a plan is a debt the person owes, with a schedule of
expected payments. The schedule never posts anything; what was paid comes from the ledger (the
debt's balance and the linked interest and fee expenses), so it can't drift from the money.

Trip planning (§11.12): bookings, transport and ideas, with estimates that are never spending, plus
an optional shared budget per trip and a private budget per member.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.finance import CurrencyFk
from app.db.models.identity import CreatedAt, UpdatedAt, UserFk, UuidPk

PLAN_STATUSES = ("active", "archived")
PLAN_ITEM_KINDS = ("accommodation", "transport", "activity", "note", "link")
TRANSPORT_MODES = ("flight", "train", "bus", "car", "ferry", "other")
BOOKING_STATUSES = ("idea", "planned", "booked", "cancelled")


class InstallmentPlan(Base):
    """A purchase or loan paid in installments. Its debt holds what remains (never stored)."""

    __tablename__ = "installment_plans"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("debt_id"),
        ForeignKeyConstraint(
            ["debt_id", "user_id"], ["debts.id", "debts.user_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["purchase_transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["goal_id", "user_id"],
            ["savings_goals.id", "savings_goals.user_id"],
            ondelete="RESTRICT",
        ),
        Index("ix_installment_plans_user", "user_id", "status"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    debt_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    lender: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[CurrencyFk]
    # What the thing cost and what was paid upfront, as the agreement says. Informational: the
    # money is in the purchase transaction and the debt.
    item_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    down_payment_minor: Mapped[int | None] = mapped_column(BigInteger)
    purchase_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    installment_count: Mapped[int] = mapped_column(SmallInteger)
    # Installments paid before the plan was recorded in FinPilot (not in the ledger).
    paid_before_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    interest_mode: Mapped[str] = mapped_column(Text, server_default="none")
    goal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class InstallmentItem(Base):
    """One expected installment. Rescheduling supersedes rows; it never edits or deletes them."""

    __tablename__ = "installment_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "user_id"],
            ["installment_plans.id", "installment_plans.user_id"],
            ondelete="RESTRICT",
        ),
        Index(
            "uq_installment_items_live_sequence",
            "plan_id",
            "sequence",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index("ix_installment_items_user_due", "user_id", "due_date"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(SmallInteger)
    due_date: Mapped[date] = mapped_column(Date)
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    interest_minor: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    fee_minor: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    superseded_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]


class InstallmentPayment(Base):
    """One confirmed payment: the principal repayment and any interest or fee expense with it."""

    __tablename__ = "installment_payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "user_id"],
            ["installment_plans.id", "installment_plans.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["principal_transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["interest_transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["fee_transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("principal_transaction_id"),
        UniqueConstraint("interest_transaction_id"),
        UniqueConstraint("fee_transaction_id"),
        Index("ix_installment_payments_plan", "plan_id", "paid_on"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    paid_on: Mapped[date] = mapped_column(Date)
    principal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    interest_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    fee_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[CreatedAt]


class TripPlanItem(Base):
    """A booking, a journey, an activity, a note or a link. Estimates are never spending."""

    __tablename__ = "trip_plan_items"
    __table_args__ = (Index("ix_trip_plan_items_trip", "trip_id", "kind"),)

    id: Mapped[UuidPk]
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    transport_mode: Mapped[str | None] = mapped_column(Text)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    booking_status: Mapped[str] = mapped_column(Text, server_default="planned")
    # In the trip's base currency.
    estimated_minor: Mapped[int | None] = mapped_column(BigInteger)
    deposit_minor: Mapped[int | None] = mapped_column(BigInteger)
    reference: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    # The real cost, once recorded: a trip expense (counted only when approved).
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("trip_expenses.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UserFk]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class TripPersonalBudget(Base):
    """One member's private budget for a trip. Only that member can read or change it."""

    __tablename__ = "trip_personal_budgets"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trips.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
