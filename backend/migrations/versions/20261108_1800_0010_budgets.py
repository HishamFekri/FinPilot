"""Monthly budgets (FINANCIAL_RULES.md §6, DATABASE.md budgets).

M6. Additive: one new table; no existing row or definition changes.

A budget is a series `(user, category or overall, currency)` of effective-dated rows. The limit for a
month is the row with the latest `effective_from_month` on or before it. Changing a limit from June
writes a row from June, so May's figures never move. A row with no amount ends the budget from
that month (archived); a later row with an amount brings it back. `alert_percent` is when the
budget shows "approaching" (80 by default).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("effective_from_month", sa.Date(), nullable=False),
        sa.Column("alert_percent", sa.SmallInteger(), server_default="80", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            f"amount_minor IS NULL OR (amount_minor > 0 AND amount_minor <= {MAX_MINOR})",
            name=op.f("ck_budgets_amount_range"),
        ),
        sa.CheckConstraint(
            "extract(day FROM effective_from_month) = 1", name=op.f("ck_budgets_month_start")
        ),
        sa.CheckConstraint("alert_percent BETWEEN 1 AND 100", name=op.f("ck_budgets_alert_range")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_budgets_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["currency"], ["currencies.code"], name=op.f("fk_budgets_currency_currencies"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["category_id", "user_id"], ["categories.id", "categories.user_id"],
            name=op.f("fk_budgets_category_id_categories"), ondelete="RESTRICT",
        ),  # fmt: skip
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
    )
    op.create_index(
        "uq_budgets_overall_month",
        "budgets",
        ["user_id", "currency", "effective_from_month"],
        unique=True,
        postgresql_where=sa.text("category_id IS NULL"),
    )
    op.create_index(
        "uq_budgets_category_month",
        "budgets",
        ["user_id", "category_id", "currency", "effective_from_month"],
        unique=True,
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )
    op.execute(
        "CREATE TRIGGER trg_budgets_set_updated_at BEFORE UPDATE ON budgets "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
    )


def downgrade() -> None:
    # Test databases only: the upgrade path is forward.
    op.execute("DROP TRIGGER trg_budgets_set_updated_at ON budgets")
    op.drop_table("budgets")
