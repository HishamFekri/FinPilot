"""Owner-only links between trip records and the owner's own ledger (FINANCIAL_RULES.md §11).

Readable only by their owner; no trip response ever reveals whether one exists. Constraints in
migration 0013.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import CreatedAt, UserFk, UuidPk

LINK_KINDS = ("expense_payment", "settlement_paid", "settlement_received")


class PersonalLink(Base):
    __tablename__ = "personal_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "owner_user_id"],
            ["transactions.id", "transactions.user_id"],
            name="fk_personal_links_own_transaction",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["expense_id", "trip_id"],
            ["trip_expenses.id", "trip_expenses.trip_id"],
            name="fk_personal_links_expense",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["settlement_id", "trip_id"],
            ["settlements.id", "settlements.trip_id"],
            name="fk_personal_links_settlement",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_personal_links_owner_expense",
            "owner_user_id",
            "expense_id",
            unique=True,
            postgresql_where=text("status = 'active' AND expense_id IS NOT NULL"),
        ),
        Index(
            "uq_personal_links_owner_settlement",
            "owner_user_id",
            "settlement_id",
            unique=True,
            postgresql_where=text("status = 'active' AND settlement_id IS NOT NULL"),
        ),
        Index(
            "uq_personal_links_transaction",
            "transaction_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_personal_links_owner_trip",
            "owner_user_id",
            "trip_id",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UuidPk]
    owner_user_id: Mapped[UserFk]
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(Text)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    settlement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    linked_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("trip_expense_versions.id", ondelete="RESTRICT")
    )
    linked_base_amount_minor: Mapped[int] = mapped_column(BigInteger)
    base_currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currencies.code", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(Text, server_default="active")
    reversal_kept_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    resolved_at: Mapped[datetime | None]


class DuplicateDismissal(Base):
    __tablename__ = "duplicate_dismissals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            name="fk_duplicate_dismissals_own_transaction",
            ondelete="RESTRICT",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    expense_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trip_expenses.id", ondelete="RESTRICT"), primary_key=True
    )
    dismissed_at: Mapped[CreatedAt]
