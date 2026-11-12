"""Ledger core: currencies, financial accounts, ledger accounts, transactions, and the journal.

Rules: docs/FINANCIAL_RULES.md §2 and §4, docs/DATABASE.md §3.1 and §3.3. The constraints, composite
foreign keys, and triggers are defined in migration 0005; these classes map the same tables so the
application can read and write them. The database, not this module, is the last line of defence.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import CreatedAt, UpdatedAt, UserFk, UuidPk

CurrencyFk = Annotated[
    str, mapped_column(CHAR(3), ForeignKey("currencies.code", ondelete="RESTRICT"))
]

ACCOUNT_TYPES = ("cash", "bank", "savings", "wallet", "other")
ACCOUNT_STATUSES = ("active", "archived")
MAX_MINOR = 10**15


class Currency(Base):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    exponent: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))


class FinancialAccount(Base):
    """A manually tracked account. There is deliberately no balance column."""

    __tablename__ = "financial_accounts"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("id", "user_id", "currency"),
        Index(
            "uq_financial_accounts_active_name",
            "user_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(None, "user_id", "status"),
        Index(
            "uq_financial_accounts_user_system_key",
            "user_id",
            "system_key",
            unique=True,
            postgresql_where=text("system_key IS NOT NULL"),
        ),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    # Empty for the automatic Cash and Bank accounts until renamed: the app shows their name in the
    # reader's language, from `system_key`.
    name: Mapped[str | None] = mapped_column(Text)
    system_key: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    currency: Mapped[CurrencyFk]
    status: Mapped[str] = mapped_column(Text, server_default="active")
    opened_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class Category(Base):
    """One of a user's categories. Seeded defaults carry a `system_key` (their translated name) and
    may be renamed; custom ones have a `name`. Archived ones stay on past transactions."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("id", "user_id", "kind"),
        # Everyday features: an expense category may belong to one of its owner's groups.
        ForeignKeyConstraint(
            ["group_id", "user_id"],
            ["category_groups.id", "category_groups.user_id"],
            name="fk_categories_group_id_user_id_category_groups",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_categories_user_system_key",
            "user_id",
            "system_key",
            unique=True,
            postgresql_where=text("system_key IS NOT NULL"),
        ),
        Index(
            "uq_categories_active_name",
            "user_id",
            "kind",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("name IS NOT NULL AND status = 'active'"),
        ),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    kind: Mapped[str] = mapped_column(Text)
    system_key: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("100"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Pinned to the front of Quick Add's category picker (migration 0016).
    is_favourite: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["financial_account_id", "user_id", "currency"],
            ["financial_accounts.id", "financial_accounts.user_id", "financial_accounts.currency"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["debt_id", "user_id", "currency"],
            ["debts.id", "debts.user_id", "debts.currency"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "user_id", "currency"),
        Index(
            "uq_ledger_accounts_debt",
            "debt_id",
            unique=True,
            postgresql_where=text("debt_id IS NOT NULL"),
        ),
        Index(
            "uq_ledger_accounts_financial_account",
            "financial_account_id",
            unique=True,
            postgresql_where=text("financial_account_id IS NOT NULL"),
        ),
        Index(
            "uq_ledger_accounts_user_kind_currency",
            "user_id",
            "kind",
            "currency",
            unique=True,
            postgresql_where=text("financial_account_id IS NULL AND debt_id IS NULL"),
        ),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    kind: Mapped[str] = mapped_column(Text)
    currency: Mapped[CurrencyFk]
    financial_account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # M5.6: a debt's own ledger account (debt_payable or debt_receivable).
    debt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[CreatedAt]


class Transaction(Base):
    """The user-facing document. Its journal entries hold the financial effect."""

    __tablename__ = "transactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_id", "currency"],
            ["financial_accounts.id", "financial_accounts.user_id", "financial_accounts.currency"],
            ondelete="RESTRICT",
        ),
        # A transfer's destination: the same user's account, in that account's currency.
        ForeignKeyConstraint(
            ["counter_account_id", "user_id", "counter_currency"],
            ["financial_accounts.id", "financial_accounts.user_id", "financial_accounts.currency"],
            ondelete="RESTRICT",
        ),
        # Own category, of the transaction's kind (`category_kind`: a refund uses an expense
        # category).
        ForeignKeyConstraint(
            ["category_id", "user_id", "category_kind"],
            ["categories.id", "categories.user_id", "categories.kind"],
            name="fk_transactions_category_id_user_id_category_kind_categories",
            ondelete="RESTRICT",
        ),
        # Everyday features: a refund points at the expense it refunds (same owner).
        ForeignKeyConstraint(
            ["refunds_transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            name="fk_transactions_refunds_transaction_id_user_id_transactions",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_transactions_refunds",
            "refunds_transaction_id",
            postgresql_where=text("refunds_transaction_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["debt_id", "user_id"], ["debts.id", "debts.user_id"], ondelete="RESTRICT"
        ),
        # B5: "attach existing" voids a hand-entered transaction and posts its trip_* replacement.
        ForeignKeyConstraint(
            ["replaces_transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            name="fk_transactions_replaces_transaction_id_user_id_transactions",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_transactions_replaces",
            "replaces_transaction_id",
            postgresql_where=text("replaces_transaction_id IS NOT NULL"),
        ),
        UniqueConstraint("id", "user_id"),
        Index(
            "ix_transactions_debt",
            "debt_id",
            "effective_date",
            postgresql_where=text("debt_id IS NOT NULL"),
        ),
        Index(
            "ix_transactions_category",
            "user_id",
            "category_id",
            "effective_date",
            postgresql_where=text("category_id IS NOT NULL"),
        ),
        Index(
            "ix_transactions_counter_account",
            "counter_account_id",
            "effective_date",
            postgresql_where=text("counter_account_id IS NOT NULL"),
        ),
        Index("ix_transactions_history", "user_id", text("effective_date DESC"), text("id DESC")),
        Index(None, "account_id", "effective_date"),
        Index(None, "fx_rate_id", postgresql_where=text("fx_rate_id IS NOT NULL")),
        Index(
            "uq_transactions_one_opening_balance",
            "account_id",
            unique=True,
            postgresql_where=text("type = 'opening_balance' AND status = 'posted'"),
        ),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="posted")
    # Null only for a debt that existed before FinPilot and moved no money (a check enforces it).
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[CurrencyFk]
    # Transfers only: where the money arrived, and how much, in that account's currency.
    counter_account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    counter_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    counter_currency: Mapped[str | None] = mapped_column(CHAR(3))
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    occurred_at: Mapped[datetime]
    effective_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_via: Mapped[str] = mapped_column(Text, server_default="form")
    # M5.2: an income or expense entered in another currency. The ledger posts amount_minor in the
    # account's currency; these record what was entered and how it was converted (§4.6, §7).
    original_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    original_currency: Mapped[str | None] = mapped_column(
        CHAR(3), ForeignKey("currencies.code", ondelete="RESTRICT")
    )
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(28, 12))
    fx_method: Mapped[str | None] = mapped_column(Text)
    fx_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("fx_rates.id", ondelete="RESTRICT")
    )
    # M5.6: debts. debt_amount_minor is in the debt's currency (differs only across currencies).
    debt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    debt_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    replaces_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    refunds_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Generated by the database: the category kind this row needs (expense for a refund).
    category_kind: Mapped[str | None] = mapped_column(
        Text,
        Computed(
            "CASE WHEN type IN ('expense', 'refund') THEN 'expense' "
            "WHEN type = 'income' THEN 'income' END",
            persisted=True,
        ),
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class Debt(Base):
    """A personal debt. What remains is derived from its own ledger account, never stored."""

    __tablename__ = "debts"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("id", "user_id", "currency"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk] = mapped_column(index=True)
    direction: Mapped[str] = mapped_column(Text)
    counterparty: Mapped[str] = mapped_column(Text)
    currency: Mapped[CurrencyFk]
    original_amount_minor: Mapped[int] = mapped_column(BigInteger)
    start_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class RecurringRule(Base):
    """Money expected on a schedule. Never posts by itself (FINANCIAL_RULES.md R1)."""

    __tablename__ = "recurring_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["financial_accounts.id", "financial_accounts.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["category_id", "user_id", "category_kind"],
            ["categories.id", "categories.user_id", "categories.kind"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["debt_id", "user_id"], ["debts.id", "debts.user_id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("id", "user_id"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[CurrencyFk]
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    category_kind: Mapped[str | None] = mapped_column(Text)
    debt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    frequency: Mapped[str] = mapped_column(Text)
    window_start_day: Mapped[int | None] = mapped_column(SmallInteger)
    window_end_day: Mapped[int | None] = mapped_column(SmallInteger)
    month: Mapped[int | None] = mapped_column(SmallInteger)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    total_occurrences: Mapped[int | None] = mapped_column(SmallInteger)
    generate_from: Mapped[date] = mapped_column(Date)
    remind_days_before: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    reminders: Mapped[bool] = mapped_column(server_default=text("true"))
    is_subscription: Mapped[bool] = mapped_column(server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, server_default="active")
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class RecurringOccurrence(Base):
    """One expected payment. Identity: (rule_id, window_start). Confirmed means one transaction."""

    __tablename__ = "recurring_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id", "user_id"],
            ["recurring_rules.id", "recurring_rules.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["financial_accounts.id", "financial_accounts.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("rule_id", "window_start"),
        UniqueConstraint("rule_id", "sequence"),
        UniqueConstraint("transaction_id"),
        Index("ix_recurring_occurrences_user_window", "user_id", "window_start"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    rule_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    sequence: Mapped[int]
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    expected_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[CurrencyFk]
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    decided_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default=text("1"))


class Budget(Base):
    """One effective-dated row of a monthly budget (FINANCIAL_RULES.md §6). No amount: ended."""

    __tablename__ = "budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "user_id"], ["categories.id", "categories.user_id"], ondelete="RESTRICT"
        ),
        Index(
            "uq_budgets_overall_month",
            "user_id",
            "currency",
            "effective_from_month",
            unique=True,
            postgresql_where=text("category_id IS NULL"),
        ),
        Index(
            "uq_budgets_category_month",
            "user_id",
            "category_id",
            "currency",
            "effective_from_month",
            unique=True,
            postgresql_where=text("category_id IS NOT NULL"),
        ),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    currency: Mapped[CurrencyFk]
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    effective_from_month: Mapped[date] = mapped_column(Date)
    alert_percent: Mapped[int] = mapped_column(SmallInteger, server_default="80")
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class FxRate(Base):
    """An immutable rate observation or manual rate (§7.1 of the rules): 1 base = rate quote."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        Index(
            "uq_fx_rates_provider_observation",
            "provider",
            "upstream",
            "base",
            "quote",
            "rate_date",
            unique=True,
            postgresql_where=text("source = 'provider'"),
        ),
        Index("ix_fx_rates_lookup", "base", "quote", text("rate_date DESC")),
    )

    id: Mapped[UuidPk]
    base: Mapped[str] = mapped_column(CHAR(3))
    quote: Mapped[str] = mapped_column(CHAR(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    rate_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    upstream: Mapped[str | None] = mapped_column(Text)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(server_default=func.now())


class JournalEntry(Base):
    """Immutable. Inserted only, never updated or deleted (a trigger enforces it)."""

    __tablename__ = "journal_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "user_id"],
            ["transactions.id", "transactions.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "user_id", "effective_date"),
        UniqueConstraint("reverses_entry_id"),
        Index(
            "ix_journal_entries_category",
            "user_id",
            "category_id",
            "effective_date",
            postgresql_where=text("category_id IS NOT NULL"),
        ),
        Index(None, "user_id", "effective_date"),
        Index(None, "transaction_id"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    transaction_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    transaction_type: Mapped[str] = mapped_column(Text)
    effective_date: Mapped[date] = mapped_column(Date)
    posted_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    # The category as a dimension of the whole entry; a reversal carries the same one.
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class JournalLine(Base):
    """Immutable. Debit is positive, credit negative; each entry sums to zero per currency."""

    __tablename__ = "journal_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "user_id", "effective_date"],
            ["journal_entries.id", "journal_entries.user_id", "journal_entries.effective_date"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ledger_account_id", "user_id", "currency"],
            ["ledger_accounts.id", "ledger_accounts.user_id", "ledger_accounts.currency"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_journal_lines_balance",
            "ledger_account_id",
            "effective_date",
            postgresql_include=["amount_minor"],
        ),
        Index(None, "entry_id"),
    )

    id: Mapped[UuidPk]
    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # Its owner is checked through the composite keys to the entry and the ledger account.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    effective_date: Mapped[date] = mapped_column(Date)
    ledger_account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    currency: Mapped[str] = mapped_column(CHAR(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (Index(None, "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    key: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    request_hash: Mapped[bytes] = mapped_column(BYTEA)
    response_status: Mapped[int] = mapped_column(SmallInteger)
    response_body: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]
