"""Everyday features: refunds, quick templates, savings goals, category groups, subscriptions.

Additive. No existing row changes.

- **Refunds (FINANCIAL_RULES.md §4.4, §4.11):** `transactions.type` also allows `refund`; a refund
  needs a category, like income and expense, and may carry the entered-currency memo. It points at
  the expense it refunds through `refunds_transaction_id` (same owner). The category foreign key
  used `type` as the category's kind, which can't work for a refund (an expense category on a
  `refund` row), so it now uses `category_kind`, a stored generated column: `expense` for
  `expense` and `refund`, `income` for `income`. Same rows, same guarantee; the old FK is dropped
  only after the new one exists. `created_via` also allows `template`.
- **Quick templates:** saved shortcuts. They never post anything; applying one opens Quick Add.
- **Savings goals:** a tracked target and an append-only event log (a trigger refuses UPDATE and
  DELETE). Progress is the sum of events and is never money in an account.
- **Category groups:** per-user groups of expense categories; `categories.group_id` (same owner).
  Defaults are seeded by the application on first use, not here.
- **Subscriptions:** `recurring_rules.is_subscription` marks rules shown in the subscriptions view;
  scheduling, reminders and confirmation stay the recurring engine's.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15
BASE_TYPES = (
    "'opening_balance', 'adjustment', 'transfer', 'income', 'expense', 'debt_start', "
    "'debt_payment', 'trip_expense_payment', 'trip_settlement_paid', 'trip_settlement_received'"
)
OLD_TYPES = f"type IN ({BASE_TYPES})"
NEW_TYPES = f"type IN ({BASE_TYPES}, 'refund')"
OLD_CATEGORY = "(type IN ('income', 'expense')) = (category_id IS NOT NULL)"
NEW_CATEGORY = "(type IN ('income', 'expense', 'refund')) = (category_id IS NOT NULL)"
CONVERSION = (
    "original_amount_minor IS NULL OR ("
    f"original_amount_minor > 0 AND original_amount_minor <= {MAX_MINOR} "
    "AND original_currency <> currency AND fx_rate > 0 "
    "AND fx_method IN ('reference', 'manual_rate', 'actual_amount') "
    "AND (fx_method = 'actual_amount' OR fx_rate_id IS NOT NULL) "
    "AND type IN ({types}))"
)
TRIP = "'trip_expense_payment', 'trip_settlement_paid', 'trip_settlement_received'"
OLD_CONVERSION = CONVERSION.format(types=f"'income', 'expense', {TRIP}")
NEW_CONVERSION = CONVERSION.format(types=f"'income', 'expense', 'refund', {TRIP}")
OLD_VIA = "created_via IN ('form', 'quick_add', 'link', 'recurring')"
NEW_VIA = "created_via IN ('form', 'quick_add', 'link', 'recurring', 'template')"
OLD_CATEGORY_FK = "fk_transactions_category_id_user_id_type_categories"
NEW_CATEGORY_FK = "fk_transactions_category_id_user_id_category_kind_categories"
CLEAN = "{c} = btrim({c}) AND char_length({c}) BETWEEN 1 AND {n} AND {c} !~ '[\\x01-\\x1f\\x7f]'"


def _swap_check(name: str, old: str, new: str) -> None:
    op.drop_constraint(op.f(name), "transactions", type_="check")
    op.create_check_constraint(op.f(name), "transactions", new)
    del old


def upgrade() -> None:
    # --- Refunds
    _swap_check("ck_transactions_type_allowed", OLD_TYPES, NEW_TYPES)
    _swap_check("ck_transactions_category_when_needed", OLD_CATEGORY, NEW_CATEGORY)
    _swap_check("ck_transactions_conversion_valid", OLD_CONVERSION, NEW_CONVERSION)
    _swap_check("ck_transactions_created_via_allowed", OLD_VIA, NEW_VIA)
    op.add_column(
        "transactions",
        sa.Column(
            "category_kind",
            sa.Text(),
            sa.Computed(
                "CASE WHEN type IN ('expense', 'refund') THEN 'expense' "
                "WHEN type = 'income' THEN 'income' END",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        NEW_CATEGORY_FK, "transactions", "categories",
        ["category_id", "user_id", "category_kind"], ["id", "user_id", "kind"],
        ondelete="RESTRICT",
    )  # fmt: skip
    op.drop_constraint(OLD_CATEGORY_FK, "transactions", type_="foreignkey")
    op.add_column("transactions", sa.Column("refunds_transaction_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_transactions_refunds_transaction_id_user_id_transactions",
        "transactions", "transactions",
        ["refunds_transaction_id", "user_id"], ["id", "user_id"], ondelete="RESTRICT",
    )  # fmt: skip
    op.create_check_constraint(
        op.f("ck_transactions_refund_target"),
        "transactions",
        "(type = 'refund') = (refunds_transaction_id IS NOT NULL)",
    )
    op.create_index(
        "ix_transactions_refunds", "transactions", ["refunds_transaction_id"],
        postgresql_where=sa.text("refunds_transaction_id IS NOT NULL"),
    )  # fmt: skip

    ts = sa.TIMESTAMP(timezone=True)

    # --- Category groups
    op.create_table(
        "category_groups",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("system_key", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("100"), nullable=False),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("(system_key IS NOT NULL) OR (name IS NOT NULL)", name=op.f("ck_category_groups_named")),
        sa.CheckConstraint("system_key IS NULL OR system_key ~ '^[a-z][a-z_]{1,39}$'", name=op.f("ck_category_groups_system_key_format")),
        sa.CheckConstraint("name IS NULL OR (" + CLEAN.format(c="name", n=40) + ")", name=op.f("ck_category_groups_name_clean")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_category_groups_status_allowed")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_category_groups_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_category_groups_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category_groups")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_category_groups_id_user_id")),
    )  # fmt: skip
    op.create_index("uq_category_groups_user_system_key", "category_groups", ["user_id", "system_key"], unique=True, postgresql_where=sa.text("system_key IS NOT NULL"))  # fmt: skip
    op.create_index("uq_category_groups_active_name", "category_groups", ["user_id", sa.text("lower(name)")], unique=True, postgresql_where=sa.text("name IS NOT NULL AND status = 'active'"))  # fmt: skip
    op.add_column("categories", sa.Column("group_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_categories_group_id_user_id_category_groups", "categories", "category_groups",
        ["group_id", "user_id"], ["id", "user_id"], ondelete="RESTRICT",
    )  # fmt: skip
    op.create_check_constraint(
        op.f("ck_categories_group_for_expenses"), "categories",
        "group_id IS NULL OR kind = 'expense'",
    )  # fmt: skip

    # --- Quick templates
    op.create_table(
        "quick_templates",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_favourite", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("use_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_used_at", ts, nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(CLEAN.format(c="name", n=60), name=op.f("ck_quick_templates_name_clean")),
        sa.CheckConstraint("kind IN ('income', 'expense')", name=op.f("ck_quick_templates_kind_allowed")),
        sa.CheckConstraint(f"amount_minor IS NULL OR (amount_minor > 0 AND amount_minor <= {MAX_MINOR})", name=op.f("ck_quick_templates_amount_range")),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 200", name=op.f("ck_quick_templates_description_length")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_quick_templates_status_allowed")),
        sa.CheckConstraint("use_count >= 0", name=op.f("ck_quick_templates_use_count_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_quick_templates_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_quick_templates_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_quick_templates_currency_currencies"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id", "user_id", "currency"], ["financial_accounts.id", "financial_accounts.user_id", "financial_accounts.currency"], name="fk_quick_templates_account", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id", "user_id", "kind"], ["categories.id", "categories.user_id", "categories.kind"], name="fk_quick_templates_category", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quick_templates")),
    )  # fmt: skip
    op.create_index("ix_quick_templates_user", "quick_templates", ["user_id", "status"])

    # --- Savings goals
    op.create_table(
        "savings_goals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("target_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(CLEAN.format(c="name", n=60), name=op.f("ck_savings_goals_name_clean")),
        sa.CheckConstraint(f"target_minor > 0 AND target_minor <= {MAX_MINOR}", name=op.f("ck_savings_goals_target_range")),
        sa.CheckConstraint("status IN ('active', 'paused', 'archived')", name=op.f("ck_savings_goals_status_allowed")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_savings_goals_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_savings_goals_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_savings_goals_currency_currencies"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_savings_goals")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_savings_goals_id_user_id")),
    )  # fmt: skip
    op.create_index("ix_savings_goals_user", "savings_goals", ["user_id", "status"])
    op.create_table(
        "savings_goal_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("target_minor", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('created', 'added', 'reduced', 'target_changed', 'status_changed', 'renamed')", name=op.f("ck_savings_goal_events_kind_allowed")),
        sa.CheckConstraint(f"(kind IN ('added', 'reduced')) = (amount_minor IS NOT NULL) AND (amount_minor IS NULL OR (amount_minor > 0 AND amount_minor <= {MAX_MINOR}))", name=op.f("ck_savings_goal_events_amount_when_moved")),
        sa.CheckConstraint("(kind IN ('created', 'target_changed')) = (target_minor IS NOT NULL)", name=op.f("ck_savings_goal_events_target_when_set")),
        sa.CheckConstraint("(kind = 'status_changed') = (status IS NOT NULL)", name=op.f("ck_savings_goal_events_status_when_changed")),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 200", name=op.f("ck_savings_goal_events_note_length")),
        sa.ForeignKeyConstraint(["goal_id", "user_id"], ["savings_goals.id", "savings_goals.user_id"], name="fk_savings_goal_events_goal", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_savings_goal_events")),
    )  # fmt: skip
    op.create_index("ix_savings_goal_events_goal", "savings_goal_events", ["goal_id", "created_at"])
    op.execute(
        """
        CREATE FUNCTION finpilot_savings_goal_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'savings goal history is append-only' USING ERRCODE = 'check_violation';
        END;
        $$;
        CREATE TRIGGER savings_goal_events_append_only
            BEFORE UPDATE OR DELETE ON savings_goal_events
            FOR EACH ROW EXECUTE FUNCTION finpilot_savings_goal_events_append_only();
        """
    )

    # --- Subscriptions (the recurring engine's rules, marked)
    op.add_column(
        "recurring_rules",
        sa.Column("is_subscription", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_recurring_rules_subscription_shape"),
        "recurring_rules",
        "NOT is_subscription OR (kind = 'expense' AND frequency IN ('monthly', 'yearly'))",
    )


def downgrade() -> None:
    # Test databases only: the upgrade path is forward.
    op.drop_constraint(op.f("ck_recurring_rules_subscription_shape"), "recurring_rules", type_="check")
    op.drop_column("recurring_rules", "is_subscription")
    op.execute("DROP TRIGGER savings_goal_events_append_only ON savings_goal_events")
    op.execute("DROP FUNCTION finpilot_savings_goal_events_append_only()")
    op.drop_table("savings_goal_events")
    op.drop_table("savings_goals")
    op.drop_table("quick_templates")
    op.drop_constraint(op.f("ck_categories_group_for_expenses"), "categories", type_="check")
    op.drop_constraint("fk_categories_group_id_user_id_category_groups", "categories", type_="foreignkey")
    op.drop_column("categories", "group_id")
    op.drop_table("category_groups")
    op.drop_index("ix_transactions_refunds", table_name="transactions")
    op.drop_constraint(op.f("ck_transactions_refund_target"), "transactions", type_="check")
    op.drop_constraint("fk_transactions_refunds_transaction_id_user_id_transactions", "transactions", type_="foreignkey")
    op.drop_column("transactions", "refunds_transaction_id")
    op.create_foreign_key(
        OLD_CATEGORY_FK, "transactions", "categories",
        ["category_id", "user_id", "type"], ["id", "user_id", "kind"], ondelete="RESTRICT",
    )  # fmt: skip
    op.drop_constraint(NEW_CATEGORY_FK, "transactions", type_="foreignkey")
    op.drop_column("transactions", "category_kind")
    _swap_check("ck_transactions_created_via_allowed", NEW_VIA, OLD_VIA)
    _swap_check("ck_transactions_conversion_valid", NEW_CONVERSION, OLD_CONVERSION)
    _swap_check("ck_transactions_category_when_needed", NEW_CATEGORY, OLD_CATEGORY)
    _swap_check("ck_transactions_type_allowed", NEW_TYPES, OLD_TYPES)
