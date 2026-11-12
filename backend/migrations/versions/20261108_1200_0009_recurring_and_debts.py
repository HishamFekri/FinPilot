"""Recurring finances and personal debts (FINANCIAL_RULES.md §4.10 and §5a).

M5.4 to M5.6. No existing row is changed or removed. Three definitions are replaced by wider ones in
the same transaction, as in 0006 and 0007: the transaction type check, the ledger kind check, and
the unique index on shared ledger accounts (which must now leave per-debt ledger accounts out).
`transactions.account_id` becomes nullable, only for a debt that existed before FinPilot and moved
no money (a check enforces that); every existing row keeps its account.

- `debts`: one per person and counterparty, in one currency. The remaining amount is never stored:
  it's the balance of the debt's own ledger account (`debt_payable` or `debt_receivable`).
- `transactions`: `debt_start` and `debt_payment`, with `debt_id` and `debt_amount_minor` (the amount
  in the debt's currency; it differs from `amount_minor` only when an account in another currency
  paid, which posts through FX clearing with both real amounts).
- `recurring_rules` and `recurring_occurrences`: expected money. An occurrence never posts by itself;
  confirming one links exactly one transaction (unique `transaction_id`), and its identity
  `(rule_id, window_start)` is unique, so generating twice can't duplicate it.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15
OLD_TYPES = "type IN ('opening_balance', 'adjustment', 'transfer', 'income', 'expense')"
NEW_TYPES = (
    "type IN ('opening_balance', 'adjustment', 'transfer', 'income', 'expense', "
    "'debt_start', 'debt_payment')"
)
OLD_KINDS = (
    "kind IN ('asset', 'income', 'expense', 'equity_opening', 'equity_adjustment', "
    "'fx_clearing', 'trip_clearing')"
)
NEW_KINDS = (
    "kind IN ('asset', 'income', 'expense', 'equity_opening', 'equity_adjustment', "
    "'fx_clearing', 'trip_clearing', 'debt_payable', 'debt_receivable')"
)


def _clean_name(column: str, max_length: int) -> str:
    # Same rule as account and category names, written with visible escapes.
    return (
        f"{column} = btrim({column}) AND char_length({column}) BETWEEN 1 AND {max_length} "
        rf"AND {column} !~ '[\x01-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]'"
    )


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    ]


def upgrade() -> None:
    # --- Debts
    op.create_table(
        "debts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("counterparty", sa.Text(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("original_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("direction IN ('i_owe', 'owed_to_me')", name=op.f("ck_debts_direction_allowed")),
        sa.CheckConstraint(_clean_name("counterparty", 100), name=op.f("ck_debts_counterparty_clean")),
        sa.CheckConstraint(
            f"original_amount_minor > 0 AND original_amount_minor <= {MAX_MINOR}",
            name=op.f("ck_debts_amount_range"),
        ),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 500", name=op.f("ck_debts_notes_length")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_debts_status_allowed")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_debts_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_debts_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_debts_currency_currencies"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_debts")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_debts_id_user_id")),
        sa.UniqueConstraint("id", "user_id", "currency", name=op.f("uq_debts_id_user_id_currency")),
    )
    op.create_index(op.f("ix_debts_user_id"), "debts", ["user_id"])
    op.execute(
        "CREATE TRIGGER trg_debts_set_updated_at BEFORE UPDATE ON debts "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
    )

    # --- One ledger account per debt
    op.add_column("ledger_accounts", sa.Column("debt_id", sa.Uuid(), nullable=True))
    op.drop_constraint(op.f("ck_ledger_accounts_kind_allowed"), "ledger_accounts", type_="check")
    op.create_check_constraint(op.f("ck_ledger_accounts_kind_allowed"), "ledger_accounts", NEW_KINDS)
    op.create_check_constraint(
        op.f("ck_ledger_accounts_debt_has_debt"),
        "ledger_accounts",
        "(kind IN ('debt_payable', 'debt_receivable')) = (debt_id IS NOT NULL)",
    )
    op.create_foreign_key(
        op.f("fk_ledger_accounts_debt_id_debts"),
        "ledger_accounts", "debts",
        ["debt_id", "user_id", "currency"], ["id", "user_id", "currency"],
        ondelete="RESTRICT",
    )  # fmt: skip
    op.create_index(
        "uq_ledger_accounts_debt",
        "ledger_accounts",
        ["debt_id"],
        unique=True,
        postgresql_where=sa.text("debt_id IS NOT NULL"),
    )
    op.drop_index("uq_ledger_accounts_user_kind_currency", table_name="ledger_accounts")
    op.create_index(
        "uq_ledger_accounts_user_kind_currency",
        "ledger_accounts",
        ["user_id", "kind", "currency"],
        unique=True,
        postgresql_where=sa.text("financial_account_id IS NULL AND debt_id IS NULL"),
    )

    # --- Debt transactions
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", NEW_TYPES)
    op.add_column("transactions", sa.Column("debt_id", sa.Uuid(), nullable=True))
    op.add_column("transactions", sa.Column("debt_amount_minor", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_transactions_debt_id_debts"),
        "transactions", "debts", ["debt_id", "user_id"], ["id", "user_id"], ondelete="RESTRICT",
    )  # fmt: skip
    op.create_check_constraint(
        op.f("ck_transactions_debt_when_needed"),
        "transactions",
        "(type IN ('debt_start', 'debt_payment')) = (debt_id IS NOT NULL) "
        "AND (debt_id IS NULL) = (debt_amount_minor IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_transactions_debt_amount_range"),
        "transactions",
        f"debt_amount_minor IS NULL OR (debt_amount_minor > 0 AND debt_amount_minor <= {MAX_MINOR})",
    )
    op.alter_column("transactions", "account_id", existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint(
        op.f("ck_transactions_account_when_needed"),
        "transactions",
        "account_id IS NOT NULL OR type = 'debt_start'",
    )
    op.create_index(
        op.f("ix_transactions_debt"),
        "transactions",
        ["debt_id", "effective_date"],
        postgresql_where=sa.text("debt_id IS NOT NULL"),
    )

    # --- Recurring rules
    op.create_table(
        "recurring_rules",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("category_kind", sa.Text(), nullable=True),
        sa.Column("debt_id", sa.Uuid(), nullable=True),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("window_start_day", sa.SmallInteger(), nullable=True),
        sa.Column("window_end_day", sa.SmallInteger(), nullable=True),
        sa.Column("month", sa.SmallInteger(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("total_occurrences", sa.SmallInteger(), nullable=True),
        sa.Column("generate_from", sa.Date(), nullable=False),
        sa.Column("remind_days_before", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("reminders", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('income', 'expense', 'debt_payment')", name=op.f("ck_recurring_rules_kind_allowed")
        ),
        sa.CheckConstraint(_clean_name("name", 100), name=op.f("ck_recurring_rules_name_clean")),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 500", name=op.f("ck_recurring_rules_notes_length")),
        sa.CheckConstraint(
            f"amount_minor IS NULL OR (amount_minor > 0 AND amount_minor <= {MAX_MINOR})",
            name=op.f("ck_recurring_rules_amount_range"),
        ),
        sa.CheckConstraint(
            "(kind IN ('income', 'expense')) = (category_id IS NOT NULL) "
            "AND (category_id IS NULL OR category_kind = kind) "
            "AND (category_id IS NULL) = (category_kind IS NULL)",
            name=op.f("ck_recurring_rules_category_when_needed"),
        ),
        sa.CheckConstraint(
            "(kind = 'debt_payment') = (debt_id IS NOT NULL)", name=op.f("ck_recurring_rules_debt_when_needed")
        ),
        sa.CheckConstraint(
            "frequency IN ('weekly', 'biweekly', 'monthly', 'yearly')",
            name=op.f("ck_recurring_rules_frequency_allowed"),
        ),
        sa.CheckConstraint(
            "CASE WHEN frequency IN ('monthly', 'yearly') THEN "
            "window_start_day BETWEEN 1 AND 31 AND window_end_day BETWEEN window_start_day AND 31 "
            "ELSE window_start_day IS NULL AND window_end_day IS NULL END",
            name=op.f("ck_recurring_rules_window_days"),
        ),
        sa.CheckConstraint(
            "(frequency = 'yearly') = (month IS NOT NULL) AND (month IS NULL OR month BETWEEN 1 AND 12)",
            name=op.f("ck_recurring_rules_month_when_yearly"),
        ),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name=op.f("ck_recurring_rules_end_after_start")),
        sa.CheckConstraint(
            "total_occurrences IS NULL OR total_occurrences BETWEEN 1 AND 600",
            name=op.f("ck_recurring_rules_total_range"),
        ),
        sa.CheckConstraint("remind_days_before BETWEEN 0 AND 14", name=op.f("ck_recurring_rules_remind_range")),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'ended')", name=op.f("ck_recurring_rules_status_allowed")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_recurring_rules_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_recurring_rules_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["currency"], ["currencies.code"], name=op.f("fk_recurring_rules_currency_currencies"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "user_id"], ["financial_accounts.id", "financial_accounts.user_id"],
            name=op.f("fk_recurring_rules_account_id_financial_accounts"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.ForeignKeyConstraint(
            ["category_id", "user_id", "category_kind"], ["categories.id", "categories.user_id", "categories.kind"],
            name=op.f("fk_recurring_rules_category_id_categories"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.ForeignKeyConstraint(
            ["debt_id", "user_id"], ["debts.id", "debts.user_id"],
            name=op.f("fk_recurring_rules_debt_id_debts"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recurring_rules")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_recurring_rules_id_user_id")),
    )
    op.create_index(op.f("ix_recurring_rules_user_id"), "recurring_rules", ["user_id"])
    op.execute(
        "CREATE TRIGGER trg_recurring_rules_set_updated_at BEFORE UPDATE ON recurring_rules "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
    )

    # --- Occurrences
    op.create_table(
        "recurring_occurrences",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("expected_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=True),
        sa.Column("snoozed_until", sa.Date(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("window_end >= window_start", name=op.f("ck_recurring_occurrences_window_order")),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_recurring_occurrences_sequence_positive")),
        sa.CheckConstraint(
            f"expected_amount_minor IS NULL OR (expected_amount_minor > 0 AND expected_amount_minor <= {MAX_MINOR})",
            name=op.f("ck_recurring_occurrences_amount_range"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'skipped')", name=op.f("ck_recurring_occurrences_status_allowed")
        ),
        sa.CheckConstraint(
            "(status = 'confirmed') = (transaction_id IS NOT NULL)",
            name=op.f("ck_recurring_occurrences_confirmed_has_transaction"),
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)", name=op.f("ck_recurring_occurrences_decided")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_recurring_occurrences_version_positive")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_recurring_occurrences_user_id_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["rule_id", "user_id"], ["recurring_rules.id", "recurring_rules.user_id"],
            name=op.f("fk_recurring_occurrences_rule_id_recurring_rules"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.ForeignKeyConstraint(
            ["account_id", "user_id"], ["financial_accounts.id", "financial_accounts.user_id"],
            name=op.f("fk_recurring_occurrences_account_id_financial_accounts"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.ForeignKeyConstraint(
            ["transaction_id", "user_id"], ["transactions.id", "transactions.user_id"],
            name=op.f("fk_recurring_occurrences_transaction_id_transactions"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.ForeignKeyConstraint(
            ["currency"], ["currencies.code"],
            name=op.f("fk_recurring_occurrences_currency_currencies"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recurring_occurrences")),
        sa.UniqueConstraint("rule_id", "window_start", name=op.f("uq_recurring_occurrences_rule_id_window_start")),
        sa.UniqueConstraint("rule_id", "sequence", name=op.f("uq_recurring_occurrences_rule_id_sequence")),
        sa.UniqueConstraint("transaction_id", name=op.f("uq_recurring_occurrences_transaction_id")),
    )
    op.create_index(
        "ix_recurring_occurrences_user_window", "recurring_occurrences", ["user_id", "window_start"]
    )
    op.execute(
        "CREATE TRIGGER trg_recurring_occurrences_set_updated_at BEFORE UPDATE ON recurring_occurrences "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
    )


def downgrade() -> None:
    # Test databases only: the upgrade path is forward.
    op.execute("DROP TRIGGER trg_recurring_occurrences_set_updated_at ON recurring_occurrences")
    op.drop_table("recurring_occurrences")
    op.execute("DROP TRIGGER trg_recurring_rules_set_updated_at ON recurring_rules")
    op.drop_table("recurring_rules")
    op.drop_index(op.f("ix_transactions_debt"), table_name="transactions")
    op.drop_constraint(op.f("ck_transactions_account_when_needed"), "transactions", type_="check")
    op.alter_column("transactions", "account_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint(op.f("ck_transactions_debt_amount_range"), "transactions", type_="check")
    op.drop_constraint(op.f("ck_transactions_debt_when_needed"), "transactions", type_="check")
    op.drop_constraint(op.f("fk_transactions_debt_id_debts"), "transactions", type_="foreignkey")
    op.drop_column("transactions", "debt_amount_minor")
    op.drop_column("transactions", "debt_id")
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", OLD_TYPES)
    op.drop_index("uq_ledger_accounts_user_kind_currency", table_name="ledger_accounts")
    op.create_index(
        "uq_ledger_accounts_user_kind_currency",
        "ledger_accounts",
        ["user_id", "kind", "currency"],
        unique=True,
        postgresql_where=sa.text("financial_account_id IS NULL"),
    )
    op.drop_index("uq_ledger_accounts_debt", table_name="ledger_accounts")
    op.drop_constraint(op.f("fk_ledger_accounts_debt_id_debts"), "ledger_accounts", type_="foreignkey")
    op.drop_constraint(op.f("ck_ledger_accounts_debt_has_debt"), "ledger_accounts", type_="check")
    op.drop_constraint(op.f("ck_ledger_accounts_kind_allowed"), "ledger_accounts", type_="check")
    op.create_check_constraint(op.f("ck_ledger_accounts_kind_allowed"), "ledger_accounts", OLD_KINDS)
    op.drop_column("ledger_accounts", "debt_id")
    op.execute("DROP TRIGGER trg_debts_set_updated_at ON debts")
    op.drop_table("debts")
