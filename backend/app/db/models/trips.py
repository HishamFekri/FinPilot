"""Trips: members, invitations, and shared expenses with immutable versions and shares.

Rules: docs/FINANCIAL_RULES.md §8, §9, §12; docs/DATABASE.md §3.5; docs/SECURITY.md §4. The
checks, the version state machine, immutability, and the share-sum check (INV-09) live in
migration 0011; these classes map the same tables.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.finance import CurrencyFk
from app.db.models.identity import CreatedAt, UpdatedAt, UserFk, UuidPk

TRIP_STATUSES = ("active", "completed", "cancelled", "archived")
TRIP_ROLES = ("owner", "admin", "member")
MEMBER_STATUSES = ("active", "left", "removed")
VERSION_STATES = ("pending", "approved", "rejected", "withdrawn", "superseded")
SPLIT_METHODS = ("equal", "fixed", "percentage")
# The shared default expense categories (A4 made category rows per user).
TRIP_CATEGORY_KEYS = (
    "food_and_drinks", "groceries", "transportation", "shopping", "housing", "bills_utilities",
    "health", "education", "entertainment", "travel", "personal_care", "subscriptions", "gifts",
    "other_expense",
)  # fmt: skip


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    destination: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    base_currency: Mapped[CurrencyFk]
    status: Mapped[str] = mapped_column(Text, server_default="active")
    # Set while cancelled, and kept through archiving, so unarchiving returns it to cancelled.
    cancelled_at: Mapped[datetime | None]
    # The group's planned total, in the base currency (0016). Members' own budgets are private.
    shared_budget_minor: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[UserFk]
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class TripMember(Base):
    """Never deleted: historical shares reference it. Rejoining reactivates the row."""

    __tablename__ = "trip_members"
    __table_args__ = (
        Index(
            "uq_trip_members_one_active_owner",
            "trip_id",
            unique=True,
            postgresql_where=text("role = 'owner' AND status = 'active'"),
        ),
        Index("ix_trip_members_user_id_status", "user_id", "status"),
    )

    trip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trips.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    joined_at: Mapped[CreatedAt]
    ended_at: Mapped[datetime | None]
    ended_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )


class TripInvitation(Base):
    __tablename__ = "trip_invitations"
    __table_args__ = (Index("ix_trip_invitations_trip_id_status", "trip_id", "status"),)

    id: Mapped[UuidPk]
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="RESTRICT"))
    token_hash: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    invited_by: Mapped[UserFk]
    invitee_label: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    expires_at: Mapped[datetime]
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]


class TripExpense(Base):
    """The stable identity of an expense. Immutable: the payer never changes (§12.5)."""

    __tablename__ = "trip_expenses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trip_id", "payer_user_id"],
            ["trip_members.trip_id", "trip_members.user_id"],
            name="fk_trip_expenses_payer_trip_members",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trip_id", "created_by"],
            ["trip_members.trip_id", "trip_members.user_id"],
            name="fk_trip_expenses_creator_trip_members",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["replaces_expense_id", "trip_id"],
            ["trip_expenses.id", "trip_expenses.trip_id"],
            name="fk_trip_expenses_replaces_trip_expenses",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "trip_id", name="uq_trip_expenses_id_trip_id"),
        Index("ix_trip_expenses_trip_id_created_at", "trip_id", text("created_at DESC")),
    )

    id: Mapped[UuidPk]
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payer_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    replaces_expense_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[CreatedAt]


class TripExpenseVersion(Base):
    """Immutable financial content; only `state` and the decision columns ever change."""

    __tablename__ = "trip_expense_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["expense_id", "trip_id"],
            ["trip_expenses.id", "trip_expenses.trip_id"],
            name="fk_trip_expense_versions_expense",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "expense_id", "version_no", name="uq_trip_expense_versions_expense_id_version_no"
        ),
        UniqueConstraint("id", "trip_id", name="uq_trip_expense_versions_id_trip_id"),
        Index(
            "uq_trip_expense_versions_one_pending",
            "expense_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index(
            "uq_trip_expense_versions_one_approved",
            "expense_id",
            unique=True,
            postgresql_where=text("state = 'approved'"),
        ),
        Index("ix_trip_expense_versions_trip_id_state", "trip_id", "state"),
    )

    id: Mapped[UuidPk]
    expense_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[UserFk]
    description: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    category_key: Mapped[str] = mapped_column(Text)
    expense_date: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[CurrencyFk]
    base_currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currencies.code", ondelete="RESTRICT")
    )
    base_amount_minor: Mapped[int] = mapped_column(BigInteger)
    rate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("fx_rates.id", ondelete="RESTRICT")
    )
    split_method: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None]
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


class ExpenseShare(Base):
    """A beneficiary's share of one version, in the original and the base currency. Immutable."""

    __tablename__ = "expense_shares"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "trip_id"],
            ["trip_expense_versions.id", "trip_expense_versions.trip_id"],
            name="fk_expense_shares_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trip_id", "user_id"],
            ["trip_members.trip_id", "trip_members.user_id"],
            name="fk_expense_shares_member",
            ondelete="RESTRICT",
        ),
        Index("ix_expense_shares_trip_id_user_id", "trip_id", "user_id"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    input_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    share_minor: Mapped[int] = mapped_column(BigInteger)
    base_share_minor: Mapped[int] = mapped_column(BigInteger)


class Settlement(Base):
    """A payment between two members, or the reversal of one (FINANCIAL_RULES.md §13).

    Only confirmed settlements count. Financial columns never change (migration 0012); the state
    moves only from pending to confirmed, rejected or cancelled.
    """

    __tablename__ = "settlements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trip_id", "payer_user_id"],
            ["trip_members.trip_id", "trip_members.user_id"],
            name="fk_settlements_payer_trip_members",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trip_id", "recipient_user_id"],
            ["trip_members.trip_id", "trip_members.user_id"],
            name="fk_settlements_recipient_trip_members",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reverses_settlement_id", "trip_id"],
            ["settlements.id", "settlements.trip_id"],
            name="fk_settlements_reverses_settlements",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["corrects_settlement_id", "trip_id"],
            ["settlements.id", "settlements.trip_id"],
            name="fk_settlements_corrects_settlements",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "trip_id", name="uq_settlements_id_trip_id"),
        Index(
            "uq_settlements_one_live_reversal",
            "reverses_settlement_id",
            unique=True,
            postgresql_where=text("state IN ('pending', 'confirmed')"),
        ),
        Index("ix_settlements_trip_id_state", "trip_id", "state"),
        Index("ix_settlements_recipient_user_id_state", "recipient_user_id", "state"),
        Index("ix_settlements_payer_user_id_state", "payer_user_id", "state"),
    )

    id: Mapped[UuidPk]
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payer_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(Text)
    reverses_settlement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    corrects_settlement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    initiated_by: Mapped[UserFk]
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[CurrencyFk]
    base_currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currencies.code", ondelete="RESTRICT")
    )
    base_amount_minor: Mapped[int] = mapped_column(BigInteger)
    rate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("fx_rates.id", ondelete="RESTRICT")
    )
    paid_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None]
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


class TripDispute(Base):
    """A beneficiary's objection to an approved version (§12.8). Never changes a balance."""

    __tablename__ = "trip_disputes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["expense_id", "trip_id"],
            ["trip_expenses.id", "trip_expenses.trip_id"],
            name="fk_trip_disputes_expense",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["version_id", "trip_id"],
            ["trip_expense_versions.id", "trip_expense_versions.trip_id"],
            name="fk_trip_disputes_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trip_id", "raised_by"],
            ["trip_members.trip_id", "trip_members.user_id"],
            name="fk_trip_disputes_raiser",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_trip_disputes_one_open_per_person",
            "version_id",
            "raised_by",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_trip_disputes_trip_id_status", "trip_id", "status"),
    )

    id: Mapped[UuidPk]
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    expense_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    raised_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
