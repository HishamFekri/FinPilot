"""Exchange rates, converted transactions, and Amount Equivalent favourites.

M5.1 and M5.2. Additive: no existing row is changed or removed.

- `fx_rates`: immutable rate observations (FINANCIAL_RULES.md §7.1). Provider rows are daily
  references, one per provider, upstream, pair and the date the source returned; manual rows are
  rates a person typed, owned by them. Rows are never updated or deleted (trigger), so a recorded
  conversion can always be reproduced. Codes are three capital letters, not a foreign key, because
  gold (XAU) is a reference unit, not a currency anyone posts in.
- `transactions`: an income or expense entered in another currency keeps the original amount and
  currency, the rate applied, how it was chosen (the reference rate, a typed rate, or the amount
  actually charged), and the rate row it came from. The ledger still posts only the account's
  currency (§4.6), so nothing is counted twice.
- `users.equivalent_targets`: the person's Amount Equivalent favourites (display only).

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15
DEFAULT_TARGETS = "'{USD,EUR,XAU_G,BTC}'::text[]"

BLOCK_RATE_CHANGE = """
CREATE FUNCTION finpilot_block_rate_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'fx_rates is append-only: a rate, once recorded, never changes'
        USING ERRCODE = 'restrict_violation';
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("base", sa.CHAR(3), nullable=False),
        sa.Column("quote", sa.CHAR(3), nullable=False),
        sa.Column("rate", sa.Numeric(28, 12), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("upstream", sa.Text(), nullable=True),
        sa.Column("entered_by", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("base ~ '^[A-Z]{3}$' AND quote ~ '^[A-Z]{3}$'", name=op.f("ck_fx_rates_codes")),
        sa.CheckConstraint("base <> quote", name=op.f("ck_fx_rates_pair_differs")),
        sa.CheckConstraint("rate > 0", name=op.f("ck_fx_rates_rate_positive")),
        sa.CheckConstraint("source IN ('provider', 'manual')", name=op.f("ck_fx_rates_source_allowed")),
        sa.CheckConstraint("(source = 'manual') = (entered_by IS NOT NULL)", name=op.f("ck_fx_rates_manual_owner")),
        sa.CheckConstraint(
            "(source = 'provider') = (provider IS NOT NULL AND upstream IS NOT NULL)",
            name=op.f("ck_fx_rates_provider_named"),
        ),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 200", name=op.f("ck_fx_rates_note_length")),
        sa.ForeignKeyConstraint(["entered_by"], ["users.id"], name=op.f("fk_fx_rates_entered_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rates")),
    )
    op.create_index(
        "uq_fx_rates_provider_observation",
        "fx_rates",
        ["provider", "upstream", "base", "quote", "rate_date"],
        unique=True,
        postgresql_where=sa.text("source = 'provider'"),
    )
    op.create_index("ix_fx_rates_lookup", "fx_rates", ["base", "quote", sa.text("rate_date DESC")])
    op.create_index(op.f("ix_fx_rates_entered_by"), "fx_rates", ["entered_by"])
    op.execute(BLOCK_RATE_CHANGE)
    op.execute(
        "CREATE TRIGGER trg_fx_rates_append_only BEFORE UPDATE OR DELETE ON fx_rates "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_block_rate_change()"
    )

    # --- Converted income and expenses
    op.add_column("transactions", sa.Column("original_amount_minor", sa.BigInteger(), nullable=True))
    op.add_column("transactions", sa.Column("original_currency", sa.CHAR(3), nullable=True))
    op.add_column("transactions", sa.Column("fx_rate", sa.Numeric(28, 12), nullable=True))
    op.add_column("transactions", sa.Column("fx_method", sa.Text(), nullable=True))
    op.add_column("transactions", sa.Column("fx_rate_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_transactions_original_currency_currencies"),
        "transactions", "currencies", ["original_currency"], ["code"], ondelete="RESTRICT",
    )  # fmt: skip
    op.create_foreign_key(
        op.f("fk_transactions_fx_rate_id_fx_rates"),
        "transactions", "fx_rates", ["fx_rate_id"], ["id"], ondelete="RESTRICT",
    )  # fmt: skip
    op.create_check_constraint(
        op.f("ck_transactions_conversion_complete"),
        "transactions",
        "(original_amount_minor IS NULL) = (original_currency IS NULL) "
        "AND (original_amount_minor IS NULL) = (fx_rate IS NULL) "
        "AND (original_amount_minor IS NULL) = (fx_method IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_transactions_conversion_valid"),
        "transactions",
        "original_amount_minor IS NULL OR ("
        f"original_amount_minor > 0 AND original_amount_minor <= {MAX_MINOR} "
        "AND original_currency <> currency AND fx_rate > 0 "
        "AND fx_method IN ('reference', 'manual_rate', 'actual_amount') "
        "AND (fx_method = 'actual_amount' OR fx_rate_id IS NOT NULL) "
        "AND type IN ('income', 'expense'))",
    )
    op.create_index(
        op.f("ix_transactions_fx_rate_id"),
        "transactions",
        ["fx_rate_id"],
        postgresql_where=sa.text("fx_rate_id IS NOT NULL"),
    )

    # --- Amount Equivalent favourites
    op.add_column(
        "users",
        sa.Column(
            "equivalent_targets",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text(DEFAULT_TARGETS),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_users_equivalent_targets_bounded"),
        "users",
        "cardinality(equivalent_targets) <= 12",
    )


def downgrade() -> None:
    # Test databases only: the upgrade path is forward.
    op.drop_constraint(op.f("ck_users_equivalent_targets_bounded"), "users", type_="check")
    op.drop_column("users", "equivalent_targets")
    op.drop_index(op.f("ix_transactions_fx_rate_id"), table_name="transactions")
    op.drop_constraint(op.f("ck_transactions_conversion_valid"), "transactions", type_="check")
    op.drop_constraint(op.f("ck_transactions_conversion_complete"), "transactions", type_="check")
    op.drop_constraint(op.f("fk_transactions_fx_rate_id_fx_rates"), "transactions", type_="foreignkey")
    op.drop_constraint(op.f("fk_transactions_original_currency_currencies"), "transactions", type_="foreignkey")
    for column in ("fx_rate_id", "fx_method", "fx_rate", "original_currency", "original_amount_minor"):
        op.drop_column("transactions", column)
    op.execute("DROP TRIGGER trg_fx_rates_append_only ON fx_rates")
    op.execute("DROP FUNCTION finpilot_block_rate_change()")
    op.drop_table("fx_rates")
