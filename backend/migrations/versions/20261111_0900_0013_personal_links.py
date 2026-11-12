"""Personal links between trips and private ledgers (FINANCIAL_RULES.md §11, Phase B, B5).

Additive. No existing row changes.

- `transactions.type` also allows `trip_expense_payment`, `trip_settlement_paid` and
  `trip_settlement_received` (§11.3). They post Trip Clearing against an account and have no
  category, income or expense line, so a reimbursement can never become income. The existing
  checks already keep them category-free, debt-free and direction-free.
- `ck_transactions_conversion_valid` also allows the `trip_*` types, so an entry in an account
  whose currency isn't the trip's keeps the trip amount and the implied rate as its memo (§11.8).
- `transactions.replaces_transaction_id`: "attach existing" voids a hand-entered transaction and
  posts its `trip_*` replacement (§11.7); this points at the voided original (same owner).
- `personal_links`: the owner-only bridge from a trip expense or settlement to the owner's own
  transaction (§11.4, §11.6). The composite foreign key makes it impossible to point at someone
  else's transaction. `status` is `active` or `unlinked`; whether a link needs review is computed
  from the trip records (a correction or reversal never writes anything personal, INV-18).
- `duplicate_dismissals`: "not a duplicate", remembered per transaction and expense (§11.7).

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15
OLD_TYPES = (
    "type IN ('opening_balance', 'adjustment', 'transfer', 'income', 'expense', "
    "'debt_start', 'debt_payment')"
)
NEW_TYPES = (
    "type IN ('opening_balance', 'adjustment', 'transfer', 'income', 'expense', "
    "'debt_start', 'debt_payment', 'trip_expense_payment', 'trip_settlement_paid', "
    "'trip_settlement_received')"
)
# A trip entry in another currency than the trip keeps the trip amount as its memo (§11.8).
CONVERSION = (
    "original_amount_minor IS NULL OR ("
    f"original_amount_minor > 0 AND original_amount_minor <= {MAX_MINOR} "
    "AND original_currency <> currency AND fx_rate > 0 "
    "AND fx_method IN ('reference', 'manual_rate', 'actual_amount') "
    "AND (fx_method = 'actual_amount' OR fx_rate_id IS NOT NULL) "
    "AND type IN ({types}))"
)
OLD_CONVERSION = CONVERSION.format(types="'income', 'expense'")
NEW_CONVERSION = CONVERSION.format(
    types="'income', 'expense', 'trip_expense_payment', 'trip_settlement_paid', "
    "'trip_settlement_received'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", NEW_TYPES)
    op.drop_constraint(op.f("ck_transactions_conversion_valid"), "transactions", type_="check")
    op.create_check_constraint(
        op.f("ck_transactions_conversion_valid"), "transactions", NEW_CONVERSION
    )
    op.add_column("transactions", sa.Column("replaces_transaction_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_transactions_replaces_transaction_id_user_id_transactions"),
        "transactions", "transactions",
        ["replaces_transaction_id", "user_id"], ["id", "user_id"], ondelete="RESTRICT",
    )  # fmt: skip
    op.create_index(
        "ix_transactions_replaces", "transactions", ["replaces_transaction_id"],
        postgresql_where=sa.text("replaces_transaction_id IS NOT NULL"),
    )  # fmt: skip

    ts = sa.TIMESTAMP(timezone=True)
    op.create_table(
        "personal_links",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("expense_id", sa.Uuid(), nullable=True),
        sa.Column("settlement_id", sa.Uuid(), nullable=True),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("linked_version_id", sa.Uuid(), nullable=True),
        sa.Column("linked_base_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("base_currency", sa.CHAR(3), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("reversal_kept_at", ts, nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", ts, nullable=True),
        sa.CheckConstraint("kind IN ('expense_payment', 'settlement_paid', 'settlement_received')", name=op.f("ck_personal_links_kind_values")),
        sa.CheckConstraint("status IN ('active', 'unlinked')", name=op.f("ck_personal_links_status_values")),
        sa.CheckConstraint("(expense_id IS NULL) <> (settlement_id IS NULL)", name=op.f("ck_personal_links_one_item")),
        sa.CheckConstraint("(kind = 'expense_payment') = (expense_id IS NOT NULL)", name=op.f("ck_personal_links_kind_matches_item")),
        sa.CheckConstraint("(kind = 'expense_payment') = (linked_version_id IS NOT NULL)", name=op.f("ck_personal_links_version_for_expenses")),
        sa.CheckConstraint(f"linked_base_amount_minor <> 0 AND abs(linked_base_amount_minor) <= {MAX_MINOR}", name=op.f("ck_personal_links_amount_range")),
        sa.CheckConstraint("(kind = 'settlement_received') = (linked_base_amount_minor < 0)", name=op.f("ck_personal_links_sign")),
        sa.CheckConstraint("(status = 'unlinked') = (resolved_at IS NOT NULL)", name=op.f("ck_personal_links_resolved_when_unlinked")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=op.f("fk_personal_links_owner_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], name=op.f("fk_personal_links_trip_id_trips"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transaction_id", "owner_user_id"], ["transactions.id", "transactions.user_id"], name=op.f("fk_personal_links_own_transaction"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["expense_id", "trip_id"], ["trip_expenses.id", "trip_expenses.trip_id"], name=op.f("fk_personal_links_expense"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["settlement_id", "trip_id"], ["settlements.id", "settlements.trip_id"], name=op.f("fk_personal_links_settlement"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["linked_version_id"], ["trip_expense_versions.id"], name=op.f("fk_personal_links_linked_version_id_trip_expense_versions"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_currency"], ["currencies.code"], name=op.f("fk_personal_links_base_currency_currencies"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_personal_links")),
    )  # fmt: skip
    live = sa.text("status = 'active'")
    op.create_index("uq_personal_links_owner_expense", "personal_links", ["owner_user_id", "expense_id"], unique=True, postgresql_where=sa.text("status = 'active' AND expense_id IS NOT NULL"))  # fmt: skip
    op.create_index("uq_personal_links_owner_settlement", "personal_links", ["owner_user_id", "settlement_id"], unique=True, postgresql_where=sa.text("status = 'active' AND settlement_id IS NOT NULL"))  # fmt: skip
    op.create_index("uq_personal_links_transaction", "personal_links", ["transaction_id"], unique=True, postgresql_where=live)  # fmt: skip
    op.create_index("ix_personal_links_owner_trip", "personal_links", ["owner_user_id", "trip_id"], postgresql_where=live)  # fmt: skip

    op.create_table(
        "duplicate_dismissals",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("expense_id", sa.Uuid(), nullable=False),
        sa.Column("dismissed_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id", "user_id"], ["transactions.id", "transactions.user_id"], name=op.f("fk_duplicate_dismissals_own_transaction"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_duplicate_dismissals_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["expense_id"], ["trip_expenses.id"], name=op.f("fk_duplicate_dismissals_expense_id_trip_expenses"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", "transaction_id", "expense_id", name=op.f("pk_duplicate_dismissals")),
    )  # fmt: skip


def downgrade() -> None:
    # Test databases only: the upgrade path is forward.
    op.drop_table("duplicate_dismissals")
    op.drop_table("personal_links")
    op.drop_index("ix_transactions_replaces", table_name="transactions")
    op.drop_constraint(
        op.f("fk_transactions_replaces_transaction_id_user_id_transactions"),
        "transactions",
        type_="foreignkey",
    )
    op.drop_column("transactions", "replaces_transaction_id")
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", OLD_TYPES)
    op.drop_constraint(op.f("ck_transactions_conversion_valid"), "transactions", type_="check")
    op.create_check_constraint(
        op.f("ck_transactions_conversion_valid"), "transactions", OLD_CONVERSION
    )
