"""Allow adjustments and transfers between a user's own accounts. Additive only.

A3.2. Widens `transactions.type` from opening balances to `adjustment` and `transfer`, and adds the
transfer's other side: the destination account, and the amount that arrived there in its own
currency (FINANCIAL_RULES.md §4.4, §4.5). No existing row changes, and every existing row already
satisfies the new checks (it has no counter account).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15
OLD_TYPES = "type IN ('opening_balance')"
NEW_TYPES = "type IN ('opening_balance', 'adjustment', 'transfer')"


def upgrade() -> None:
    # Replacing a check with a wider one keeps every existing row valid.
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", NEW_TYPES)

    op.add_column("transactions", sa.Column("counter_account_id", sa.Uuid(), nullable=True))
    op.add_column("transactions", sa.Column("counter_amount_minor", sa.BigInteger(), nullable=True))
    op.add_column("transactions", sa.Column("counter_currency", sa.CHAR(3), nullable=True))

    op.create_check_constraint(
        op.f("ck_transactions_transfer_has_counter"),
        "transactions",
        "(type = 'transfer') = (counter_account_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_transactions_counter_complete"),
        "transactions",
        "(counter_account_id IS NULL) = (counter_amount_minor IS NULL) "
        "AND (counter_account_id IS NULL) = (counter_currency IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_transactions_counter_is_another_account"),
        "transactions",
        "counter_account_id IS NULL OR counter_account_id <> account_id",
    )
    op.create_check_constraint(
        op.f("ck_transactions_counter_amount_range"),
        "transactions",
        f"counter_amount_minor IS NULL OR (counter_amount_minor > 0 "
        f"AND counter_amount_minor <= {MAX_MINOR})",
    )
    # The same currency on both sides means the same amount on both sides: a same-currency
    # transfer can't create or destroy money.
    op.create_check_constraint(
        op.f("ck_transactions_same_currency_same_amount"),
        "transactions",
        "counter_currency IS NULL OR counter_currency <> currency "
        "OR counter_amount_minor = amount_minor",
    )
    # The destination is the same user's account, in its own currency.
    op.create_foreign_key(
        op.f("fk_transactions_counter_account_id_user_id_counter_currency_financial_accounts"),
        "transactions",
        "financial_accounts",
        ["counter_account_id", "user_id", "counter_currency"],
        ["id", "user_id", "currency"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_transactions_counter_account",
        "transactions",
        ["counter_account_id", "effective_date"],
        postgresql_where=sa.text("counter_account_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_counter_account", table_name="transactions")
    op.drop_constraint(
        op.f("fk_transactions_counter_account_id_user_id_counter_currency_financial_accounts"),
        "transactions",
        type_="foreignkey",
    )
    for name in (
        "same_currency_same_amount",
        "counter_amount_range",
        "counter_is_another_account",
        "counter_complete",
        "transfer_has_counter",
    ):
        op.drop_constraint(op.f(f"ck_transactions_{name}"), "transactions", type_="check")
    op.drop_column("transactions", "counter_currency")
    op.drop_column("transactions", "counter_amount_minor")
    op.drop_column("transactions", "counter_account_id")
    op.drop_constraint(op.f("ck_transactions_type_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_type_allowed"), "transactions", OLD_TYPES)
