"""Everyday features: category groups, quick templates, savings goals (migration 0014).

None of these posts money. A template opens Quick Add; a goal's progress is a tracked number,
never money in an account; a group only organises categories.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import CreatedAt, UpdatedAt, UserFk, UuidPk

GROUP_STATUSES = ("active", "archived")
TEMPLATE_KINDS = ("income", "expense")
GOAL_STATUSES = ("active", "paused", "archived")
GOAL_EVENT_KINDS = ("created", "added", "reduced", "target_changed", "status_changed", "renamed")


class CategoryGroup(Base):
    __tablename__ = "category_groups"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        Index(
            "uq_category_groups_user_system_key",
            "user_id",
            "system_key",
            unique=True,
            postgresql_where=text("system_key IS NOT NULL"),
        ),
        Index(
            "uq_category_groups_active_name",
            "user_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("name IS NOT NULL AND status = 'active'"),
        ),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    system_key: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("100"))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class QuickTemplate(Base):
    __tablename__ = "quick_templates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_id", "currency"],
            ["financial_accounts.id", "financial_accounts.user_id", "financial_accounts.currency"],
            name="fk_quick_templates_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["category_id", "user_id", "kind"],
            ["categories.id", "categories.user_id", "categories.kind"],
            name="fk_quick_templates_category",
            ondelete="RESTRICT",
        ),
        Index("ix_quick_templates_user", "user_id", "status"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currencies.code", ondelete="RESTRICT")
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    description: Mapped[str | None] = mapped_column(Text)
    is_favourite: Mapped[bool] = mapped_column(server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, server_default="active")
    use_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_used_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class SavingsGoal(Base):
    """A target the person tracks by hand. Progress is never money in an account."""

    __tablename__ = "savings_goals"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        Index("ix_savings_goals_user", "user_id", "status"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    name: Mapped[str] = mapped_column(Text)
    target_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currencies.code", ondelete="RESTRICT")
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class SavingsGoalEvent(Base):
    """Append-only (a trigger refuses UPDATE and DELETE). Progress = added - reduced."""

    __tablename__ = "savings_goal_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["goal_id", "user_id"],
            ["savings_goals.id", "savings_goals.user_id"],
            name="fk_savings_goal_events_goal",
            ondelete="RESTRICT",
        ),
        Index("ix_savings_goal_events_goal", "goal_id", "created_at"),
    )

    id: Mapped[UuidPk]
    goal_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(Text)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    target_minor: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
