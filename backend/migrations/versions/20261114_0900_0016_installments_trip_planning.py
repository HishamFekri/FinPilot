"""Installment plans, trip planning and budgets, trip cancellation, favourite categories.

Additive. No existing row changes and no financial table loses anything.

- **Installment plans (FINANCIAL_RULES.md §4.13):** `installment_plans` points at the debt that holds
  what remains (its own ledger account, §4.10), at the purchase expense when the plan began with a
  purchase, and optionally at a savings goal. `installment_items` is the expected schedule: it never
  posts, and rescheduling supersedes rows instead of editing them, so the history stays.
  `installment_payments` ties each confirmed payment to its principal repayment and the separate
  interest and fee expenses.
- **Transactions:** `created_via` also allows `installment`.
- **Trips:** status also allows `cancelled`; `cancelled_at` remembers it through archiving.
  `shared_budget_minor` is the group's planned total. `trip_plan_items` holds bookings, journeys,
  activities, notes and links; estimates there are never spending. `trip_personal_budgets` is one
  member's private budget, read and written only by that member.
- **Categories:** `is_favourite` pins a category in Quick Add.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15
CLEAN = "{c} = btrim({c}) AND char_length({c}) BETWEEN 1 AND {n} AND {c} !~ '[\\x01-\\x1f\\x7f]'"
OPTIONAL = "{c} IS NULL OR (" + CLEAN + ")"
OLD_VIA = "created_via IN ('form', 'quick_add', 'link', 'recurring', 'template')"
NEW_VIA = "created_via IN ('form', 'quick_add', 'link', 'recurring', 'template', 'installment')"
OLD_TRIP_STATUS = "status IN ('active', 'completed', 'archived')"
NEW_TRIP_STATUS = "status IN ('active', 'completed', 'cancelled', 'archived')"


def _money(column: str, *, zero: bool = False) -> str:
    low = ">= 0" if zero else "> 0"
    return f"{column} IS NULL OR ({column} {low} AND {column} <= {MAX_MINOR})"


def upgrade() -> None:
    ts = sa.TIMESTAMP(timezone=True)

    op.drop_constraint(op.f("ck_transactions_created_via_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_created_via_allowed"), "transactions", NEW_VIA)

    op.add_column(
        "categories",
        sa.Column("is_favourite", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # --- Installments
    op.create_table(
        "installment_plans",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("debt_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lender", sa.Text(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("item_price_minor", sa.BigInteger(), nullable=True),
        sa.Column("down_payment_minor", sa.BigInteger(), nullable=True),
        sa.Column("purchase_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("installment_count", sa.SmallInteger(), nullable=False),
        sa.Column("paid_before_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("interest_mode", sa.Text(), server_default="none", nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(CLEAN.format(c="name", n=80), name=op.f("ck_installment_plans_name_clean")),
        sa.CheckConstraint(OPTIONAL.format(c="lender", n=80), name=op.f("ck_installment_plans_lender_clean")),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 500", name=op.f("ck_installment_plans_notes_length")),
        sa.CheckConstraint(_money("item_price_minor"), name=op.f("ck_installment_plans_price_range")),
        sa.CheckConstraint(_money("down_payment_minor", zero=True), name=op.f("ck_installment_plans_down_payment_range")),
        sa.CheckConstraint("installment_count BETWEEN 1 AND 600", name=op.f("ck_installment_plans_count_range")),
        sa.CheckConstraint("paid_before_count >= 0 AND paid_before_count < installment_count", name=op.f("ck_installment_plans_paid_before_range")),
        sa.CheckConstraint("interest_mode IN ('none', 'fixed')", name=op.f("ck_installment_plans_interest_mode_allowed")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_installment_plans_status_allowed")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_installment_plans_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_installment_plans_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_installment_plans_currency_currencies"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["debt_id", "user_id"], ["debts.id", "debts.user_id"], name=op.f("fk_installment_plans_debt_id_user_id_debts"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["purchase_transaction_id", "user_id"], ["transactions.id", "transactions.user_id"], name=op.f("fk_installment_plans_purchase_transaction_id_user_id_transactions"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["goal_id", "user_id"], ["savings_goals.id", "savings_goals.user_id"], name=op.f("fk_installment_plans_goal_id_user_id_savings_goals"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installment_plans")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_installment_plans_id_user_id")),
        sa.UniqueConstraint("debt_id", name=op.f("uq_installment_plans_debt_id")),
    )  # fmt: skip
    op.create_index("ix_installment_plans_user", "installment_plans", ["user_id", "status"])

    op.create_table(
        "installment_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.SmallInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("principal_minor", sa.BigInteger(), nullable=False),
        sa.Column("interest_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("fee_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("superseded_at", ts, nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sequence BETWEEN 1 AND 600", name=op.f("ck_installment_items_sequence_range")),
        sa.CheckConstraint(f"principal_minor >= 0 AND principal_minor <= {MAX_MINOR}", name=op.f("ck_installment_items_principal_range")),
        sa.CheckConstraint(f"interest_minor >= 0 AND interest_minor <= {MAX_MINOR}", name=op.f("ck_installment_items_interest_range")),
        sa.CheckConstraint(f"fee_minor >= 0 AND fee_minor <= {MAX_MINOR}", name=op.f("ck_installment_items_fee_range")),
        sa.CheckConstraint("principal_minor + interest_minor + fee_minor > 0", name=op.f("ck_installment_items_something_due")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_installment_items_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id", "user_id"], ["installment_plans.id", "installment_plans.user_id"], name=op.f("fk_installment_items_plan_id_user_id_installment_plans"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installment_items")),
    )  # fmt: skip
    op.create_index(
        "uq_installment_items_live_sequence", "installment_items", ["plan_id", "sequence"],
        unique=True, postgresql_where=sa.text("superseded_at IS NULL"),
    )  # fmt: skip
    op.create_index("ix_installment_items_user_due", "installment_items", ["user_id", "due_date"])
    # The schedule is history: rows are superseded, never edited (except to supersede) or deleted.
    op.execute("""
        CREATE FUNCTION finpilot_installment_items_immutable() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'installment_items rows are never deleted';
            END IF;
            IF OLD.superseded_at IS NOT NULL
               OR NEW.superseded_at IS NULL
               OR (NEW.plan_id, NEW.sequence, NEW.due_date, NEW.principal_minor,
                   NEW.interest_minor, NEW.fee_minor)
                  IS DISTINCT FROM (OLD.plan_id, OLD.sequence, OLD.due_date,
                   OLD.principal_minor, OLD.interest_minor, OLD.fee_minor) THEN
                RAISE EXCEPTION 'installment_items rows can only be superseded';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;
        CREATE TRIGGER installment_items_immutable BEFORE UPDATE OR DELETE ON installment_items
            FOR EACH ROW EXECUTE FUNCTION finpilot_installment_items_immutable();
    """)

    op.create_table(
        "installment_payments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("principal_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("interest_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("fee_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "num_nonnulls(principal_transaction_id, interest_transaction_id, fee_transaction_id) >= 1",
            name=op.f("ck_installment_payments_something_paid"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_installment_payments_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id", "user_id"], ["installment_plans.id", "installment_plans.user_id"], name=op.f("fk_installment_payments_plan_id_user_id_installment_plans"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["principal_transaction_id", "user_id"], ["transactions.id", "transactions.user_id"], name=op.f("fk_installment_payments_principal_transaction_id_user_id_transactions"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["interest_transaction_id", "user_id"], ["transactions.id", "transactions.user_id"], name=op.f("fk_installment_payments_interest_transaction_id_user_id_transactions"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fee_transaction_id", "user_id"], ["transactions.id", "transactions.user_id"], name=op.f("fk_installment_payments_fee_transaction_id_user_id_transactions"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installment_payments")),
        sa.UniqueConstraint("principal_transaction_id", name=op.f("uq_installment_payments_principal_transaction_id")),
        sa.UniqueConstraint("interest_transaction_id", name=op.f("uq_installment_payments_interest_transaction_id")),
        sa.UniqueConstraint("fee_transaction_id", name=op.f("uq_installment_payments_fee_transaction_id")),
    )  # fmt: skip
    op.create_index("ix_installment_payments_plan", "installment_payments", ["plan_id", "paid_on"])

    # --- Trips
    op.drop_constraint(op.f("ck_trips_status_values"), "trips", type_="check")
    op.create_check_constraint(op.f("ck_trips_status_values"), "trips", NEW_TRIP_STATUS)
    op.add_column("trips", sa.Column("cancelled_at", ts, nullable=True))
    op.add_column("trips", sa.Column("shared_budget_minor", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_trips_shared_budget_range"), "trips", _money("shared_budget_minor")
    )
    op.create_check_constraint(
        op.f("ck_trips_cancelled_when_cancelled"),
        "trips",
        "status <> 'cancelled' OR cancelled_at IS NOT NULL",
    )

    op.create_table(
        "trip_plan_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("transport_mode", sa.Text(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("booking_status", sa.Text(), server_default="planned", nullable=False),
        sa.Column("estimated_minor", sa.BigInteger(), nullable=True),
        sa.Column("deposit_minor", sa.BigInteger(), nullable=True),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("expense_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("kind IN ('accommodation', 'transport', 'activity', 'note', 'link')", name=op.f("ck_trip_plan_items_kind_allowed")),
        sa.CheckConstraint(CLEAN.format(c="title", n=100), name=op.f("ck_trip_plan_items_title_clean")),
        sa.CheckConstraint("transport_mode IS NULL OR (kind = 'transport' AND transport_mode IN ('flight', 'train', 'bus', 'car', 'ferry', 'other'))", name=op.f("ck_trip_plan_items_transport_mode_allowed")),
        sa.CheckConstraint("ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on", name=op.f("ck_trip_plan_items_dates_ordered")),
        sa.CheckConstraint("booking_status IN ('idea', 'planned', 'booked', 'cancelled')", name=op.f("ck_trip_plan_items_booking_status_allowed")),
        sa.CheckConstraint(_money("estimated_minor", zero=True), name=op.f("ck_trip_plan_items_estimate_range")),
        sa.CheckConstraint(_money("deposit_minor", zero=True), name=op.f("ck_trip_plan_items_deposit_range")),
        sa.CheckConstraint(OPTIONAL.format(c="reference", n=60), name=op.f("ck_trip_plan_items_reference_clean")),
        sa.CheckConstraint("url IS NULL OR (char_length(url) <= 500 AND url ~ '^https://[^[:space:]]+$')", name=op.f("ck_trip_plan_items_url_https")),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 1000", name=op.f("ck_trip_plan_items_notes_length")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_trip_plan_items_version_positive")),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], name=op.f("fk_trip_plan_items_trip_id_trips"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["expense_id"], ["trip_expenses.id"], name=op.f("fk_trip_plan_items_expense_id_trip_expenses"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_trip_plan_items_created_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trip_plan_items")),
    )  # fmt: skip
    op.create_index("ix_trip_plan_items_trip", "trip_plan_items", ["trip_id", "kind"])

    op.create_table(
        "trip_personal_budgets",
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"amount_minor > 0 AND amount_minor <= {MAX_MINOR}", name=op.f("ck_trip_personal_budgets_amount_range")),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], name=op.f("fk_trip_personal_budgets_trip_id_trips"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_trip_personal_budgets_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("trip_id", "user_id", name=op.f("pk_trip_personal_budgets")),
    )  # fmt: skip

    for table in ("installment_plans", "trip_plan_items", "trip_personal_budgets"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
        )


def downgrade() -> None:
    op.drop_table("trip_personal_budgets")
    op.drop_index("ix_trip_plan_items_trip", table_name="trip_plan_items")
    op.drop_table("trip_plan_items")
    op.drop_constraint(op.f("ck_trips_cancelled_when_cancelled"), "trips", type_="check")
    op.drop_constraint(op.f("ck_trips_shared_budget_range"), "trips", type_="check")
    op.drop_column("trips", "shared_budget_minor")
    op.drop_column("trips", "cancelled_at")
    op.drop_constraint(op.f("ck_trips_status_values"), "trips", type_="check")
    op.create_check_constraint(op.f("ck_trips_status_values"), "trips", OLD_TRIP_STATUS)
    op.drop_index("ix_installment_payments_plan", table_name="installment_payments")
    op.drop_table("installment_payments")
    op.execute("DROP TRIGGER installment_items_immutable ON installment_items")
    op.execute("DROP FUNCTION finpilot_installment_items_immutable()")
    op.drop_index("ix_installment_items_user_due", table_name="installment_items")
    op.drop_index("uq_installment_items_live_sequence", table_name="installment_items")
    op.drop_table("installment_items")
    op.drop_index("ix_installment_plans_user", table_name="installment_plans")
    op.drop_table("installment_plans")
    op.drop_column("categories", "is_favourite")
    op.drop_constraint(op.f("ck_transactions_created_via_allowed"), "transactions", type_="check")
    op.create_check_constraint(op.f("ck_transactions_created_via_allowed"), "transactions", OLD_VIA)
