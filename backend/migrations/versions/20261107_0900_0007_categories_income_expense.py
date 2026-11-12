"""Categories, income and expense transactions, default accounts, and the default-account preference.

A4. Additive: no existing row is changed or removed.

- `categories`: each user's own categories. Defaults are seeded per user with a `system_key`
  (their translated name) and may be renamed; custom ones have a `name`. One seeded row per user and
  key, enforced by a unique index, so seeding twice or concurrently can't duplicate.
- `transactions` gains `income` and `expense`, each with a category of the matching kind (the
  composite key `(category_id, user_id, type)` → `categories (id, user_id, kind)` enforces it).
- `journal_entries.category_id`: the category as a dimension of the entry (FINANCIAL_RULES.md §4.2),
  so a reversal carries the same category and category totals net out.
- `financial_accounts.system_key`: the automatic Cash and Bank accounts. Their name may stay empty,
  so the app shows it in the reader's language until the person renames it. At most one per user
  and key.
- `users.default_account_id` and `users.setup_completed_at`: the account Quick Add starts with, and
  when the one-time setup ran, so it never runs twice.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = "type IN ('opening_balance', 'adjustment', 'transfer')"
NEW_TYPES = "type IN ('opening_balance', 'adjustment', 'transfer', 'income', 'expense')"


def _clean_name(column: str, max_length: int) -> str:
    # Same rule as account names (migration 0005), written with visible escapes.
    return (
        f"{column} = btrim({column}) AND char_length({column}) BETWEEN 1 AND {max_length} "
        rf"AND {column} !~ '[\x01-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]'"
    )


def upgrade() -> None:
    # --- Categories
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("system_key", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("icon", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="100", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("kind IN ('income', 'expense')", name=op.f("ck_categories_kind_allowed")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_categories_status_allowed")),
        sa.CheckConstraint("system_key IS NOT NULL OR name IS NOT NULL", name=op.f("ck_categories_named")),
        sa.CheckConstraint("system_key IS NULL OR system_key ~ '^[a-z][a-z_]{1,39}$'", name=op.f("ck_categories_system_key_format")),
        sa.CheckConstraint(f"name IS NULL OR ({_clean_name('name', 40)})", name=op.f("ck_categories_name_clean")),
        sa.CheckConstraint("icon ~ '^[a-z][a-z0-9-]{1,39}$'", name=op.f("ck_categories_icon_format")),
        sa.CheckConstraint("color ~ '^[a-z]{3,16}$'", name=op.f("ck_categories_color_format")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_categories_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_categories_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_categories_id_user_id")),
        sa.UniqueConstraint("id", "user_id", "kind", name=op.f("uq_categories_id_user_id_kind")),
    )  # fmt: skip
    op.create_index(
        "uq_categories_user_system_key",
        "categories",
        ["user_id", "system_key"],
        unique=True,
        postgresql_where=sa.text("system_key IS NOT NULL"),
    )
    op.create_index(
        "uq_categories_active_name",
        "categories",
        ["user_id", "kind", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("name IS NOT NULL AND status = 'active'"),
    )
    op.execute(
        "CREATE TRIGGER trg_categories_set_updated_at BEFORE UPDATE ON categories "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
    )

    # --- Default accounts: a key, and a name that may stay empty until the person renames it.
    op.add_column("financial_accounts", sa.Column("system_key", sa.Text(), nullable=True))
    op.alter_column("financial_accounts", "name", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        op.f("ck_financial_accounts_named"),
        "financial_accounts",
        "name IS NOT NULL OR system_key IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_financial_accounts_system_key_allowed"),
        "financial_accounts",
        "system_key IS NULL OR system_key IN ('cash', 'bank')",
    )
    op.create_index(
        "uq_financial_accounts_user_system_key",
        "financial_accounts",
        ["user_id", "system_key"],
        unique=True,
        postgresql_where=sa.text("system_key IS NOT NULL"),
    )

    # --- The user's default account, and one-time setup.
    op.add_column("users", sa.Column("default_account_id", sa.Uuid(), nullable=True))
    op.add_column(
        "users", sa.Column("setup_completed_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_users_default_account_id_id_financial_accounts"),
        "users",
        "financial_accounts",
        ["default_account_id", "id"],
        ["id", "user_id"],
        ondelete="RESTRICT",
    )

    # --- Income and expense.
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", NEW_TYPES)
    op.add_column("transactions", sa.Column("category_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_transactions_category_when_needed"),
        "transactions",
        "(type IN ('income', 'expense')) = (category_id IS NOT NULL)",
    )
    # Own category, of the same kind as the transaction ('income' or 'expense').
    op.create_foreign_key(
        op.f("fk_transactions_category_id_user_id_type_categories"),
        "transactions",
        "categories",
        ["category_id", "user_id", "type"],
        ["id", "user_id", "kind"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_transactions_category",
        "transactions",
        ["user_id", "category_id", "effective_date"],
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )

    op.add_column("journal_entries", sa.Column("category_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_journal_entries_category_id_user_id_categories"),
        "journal_entries",
        "categories",
        ["category_id", "user_id"],
        ["id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_journal_entries_category",
        "journal_entries",
        ["user_id", "category_id", "effective_date"],
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_journal_entries_category", table_name="journal_entries")
    op.drop_constraint(
        op.f("fk_journal_entries_category_id_user_id_categories"),
        "journal_entries",
        type_="foreignkey",
    )
    op.drop_column("journal_entries", "category_id")
    op.drop_index("ix_transactions_category", table_name="transactions")
    op.drop_constraint(
        op.f("fk_transactions_category_id_user_id_type_categories"),
        "transactions",
        type_="foreignkey",
    )
    op.drop_constraint(op.f("ck_transactions_category_when_needed"), "transactions", type_="check")
    op.drop_column("transactions", "category_id")
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", OLD_TYPES)
    op.drop_constraint(
        op.f("fk_users_default_account_id_id_financial_accounts"), "users", type_="foreignkey"
    )
    op.drop_column("users", "setup_completed_at")
    op.drop_column("users", "default_account_id")
    op.drop_index("uq_financial_accounts_user_system_key", table_name="financial_accounts")
    op.drop_constraint(
        op.f("ck_financial_accounts_system_key_allowed"), "financial_accounts", type_="check"
    )
    op.drop_constraint(op.f("ck_financial_accounts_named"), "financial_accounts", type_="check")
    op.alter_column("financial_accounts", "name", existing_type=sa.Text(), nullable=False)
    op.drop_column("financial_accounts", "system_key")
    op.execute("DROP TRIGGER trg_categories_set_updated_at ON categories")
    op.drop_table("categories")
