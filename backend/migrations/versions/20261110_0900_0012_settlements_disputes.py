"""Settlements between trip members, and disputes on approved expenses (Phase B, B4).

Additive: two new tables. No existing row or definition changes.

Settlements (FINANCIAL_RULES.md §13): a payment recorded by its payer and confirmed by its
recipient, or a reversal of a confirmed payment recorded by either party and confirmed by the
other. Only confirmed settlements count. Financial columns never change; the state moves only
pending -> confirmed | rejected | cancelled. At most one live (pending or confirmed) reversal per
payment. B4 records settlements in the trip's base currency, so `rate_id` is always null for now;
the columns match DATABASE.md §3.5 so another currency can follow.

Disputes (§12.8): a beneficiary's objection to an approved version. A flag, never a balance
change. Status moves only open -> withdrawn | upheld | dismissed | closed_superseded; one open
dispute per person per version.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15

FUNCTIONS = """
CREATE FUNCTION finpilot_settlement_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'settlements are never deleted' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.trip_id, NEW.payer_user_id, NEW.recipient_user_id, NEW.kind,
        NEW.reverses_settlement_id, NEW.corrects_settlement_id, NEW.initiated_by,
        NEW.amount_minor, NEW.currency, NEW.base_currency, NEW.base_amount_minor, NEW.rate_id,
        NEW.paid_on, NEW.note, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.trip_id, OLD.payer_user_id, OLD.recipient_user_id, OLD.kind,
        OLD.reverses_settlement_id, OLD.corrects_settlement_id, OLD.initiated_by,
        OLD.amount_minor, OLD.currency, OLD.base_currency, OLD.base_amount_minor, OLD.rate_id,
        OLD.paid_on, OLD.note, OLD.created_at) THEN
        RAISE EXCEPTION 'a settlement''s content is immutable' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
        OLD.state = 'pending' AND NEW.state IN ('confirmed', 'rejected', 'cancelled')
    ) THEN
        RAISE EXCEPTION 'settlement can''t go from % to %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION finpilot_dispute_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'disputes are never deleted' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.trip_id, NEW.expense_id, NEW.version_id, NEW.raised_by, NEW.reason,
        NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.trip_id, OLD.expense_id, OLD.version_id, OLD.raised_by, OLD.reason,
        OLD.created_at) THEN
        RAISE EXCEPTION 'a dispute''s content is immutable' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        OLD.status = 'open'
        AND NEW.status IN ('withdrawn', 'upheld', 'dismissed', 'closed_superseded')
    ) THEN
        RAISE EXCEPTION 'dispute can''t go from % to %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.execute(FUNCTIONS)
    now = sa.text("now()")
    ts = sa.TIMESTAMP(timezone=True)

    op.create_table(
        "settlements",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("payer_user_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reverses_settlement_id", sa.Uuid(), nullable=True),
        sa.Column("corrects_settlement_id", sa.Uuid(), nullable=True),
        sa.Column("initiated_by", sa.Uuid(), nullable=False),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("base_currency", sa.CHAR(3), nullable=False),
        sa.Column("base_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("rate_id", sa.Uuid(), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", ts, nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", ts, server_default=now, nullable=False),
        sa.CheckConstraint("payer_user_id <> recipient_user_id", name=op.f("ck_settlements_parties_differ")),
        sa.CheckConstraint("kind IN ('payment', 'reversal')", name=op.f("ck_settlements_kind_values")),
        sa.CheckConstraint("state IN ('pending', 'confirmed', 'rejected', 'cancelled')", name=op.f("ck_settlements_state_values")),
        sa.CheckConstraint("(kind = 'reversal') = (reverses_settlement_id IS NOT NULL)", name=op.f("ck_settlements_reversal_target")),
        sa.CheckConstraint("kind <> 'payment' OR initiated_by = payer_user_id", name=op.f("ck_settlements_payment_by_payer")),
        sa.CheckConstraint("initiated_by IN (payer_user_id, recipient_user_id)", name=op.f("ck_settlements_initiated_by_party")),
        sa.CheckConstraint("kind = 'payment' OR corrects_settlement_id IS NULL", name=op.f("ck_settlements_correction_is_payment")),
        sa.CheckConstraint(f"amount_minor > 0 AND amount_minor <= {MAX_MINOR}", name=op.f("ck_settlements_amount_range")),
        sa.CheckConstraint(f"base_amount_minor > 0 AND base_amount_minor <= {MAX_MINOR}", name=op.f("ck_settlements_base_amount_range")),
        sa.CheckConstraint("(currency = base_currency) = (rate_id IS NULL)", name=op.f("ck_settlements_rate_when_converted")),
        sa.CheckConstraint("currency <> base_currency OR amount_minor = base_amount_minor", name=op.f("ck_settlements_same_currency_same_amount")),
        sa.CheckConstraint("(state = 'pending') = (decided_at IS NULL)", name=op.f("ck_settlements_decided_when_closed")),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 200", name=op.f("ck_settlements_note_length")),
        sa.CheckConstraint("decision_reason IS NULL OR char_length(decision_reason) <= 500", name=op.f("ck_settlements_reason_length")),
        sa.ForeignKeyConstraint(["trip_id", "payer_user_id"], ["trip_members.trip_id", "trip_members.user_id"], name=op.f("fk_settlements_payer_trip_members"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trip_id", "recipient_user_id"], ["trip_members.trip_id", "trip_members.user_id"], name=op.f("fk_settlements_recipient_trip_members"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reverses_settlement_id", "trip_id"], ["settlements.id", "settlements.trip_id"], name=op.f("fk_settlements_reverses_settlements"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["corrects_settlement_id", "trip_id"], ["settlements.id", "settlements.trip_id"], name=op.f("fk_settlements_corrects_settlements"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["initiated_by"], ["users.id"], name=op.f("fk_settlements_initiated_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_settlements_decided_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_settlements_currency_currencies"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_currency"], ["currencies.code"], name=op.f("fk_settlements_base_currency_currencies"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rate_id"], ["fx_rates.id"], name=op.f("fk_settlements_rate_id_fx_rates"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlements")),
        sa.UniqueConstraint("id", "trip_id", name=op.f("uq_settlements_id_trip_id")),
    )  # fmt: skip
    op.create_index(
        "uq_settlements_one_live_reversal", "settlements", ["reverses_settlement_id"],
        unique=True, postgresql_where=sa.text("state IN ('pending', 'confirmed')"),
    )  # fmt: skip
    op.create_index("ix_settlements_trip_id_state", "settlements", ["trip_id", "state"])
    op.create_index("ix_settlements_recipient_user_id_state", "settlements", ["recipient_user_id", "state"])
    op.create_index("ix_settlements_payer_user_id_state", "settlements", ["payer_user_id", "state"])
    op.execute(
        "CREATE TRIGGER trg_settlements_transition BEFORE UPDATE OR DELETE ON settlements "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_settlement_transition()"
    )

    op.create_table(
        "trip_disputes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("expense_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("raised_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", ts, nullable=True),
        sa.Column("created_at", ts, server_default=now, nullable=False),
        sa.CheckConstraint("status IN ('open', 'withdrawn', 'upheld', 'dismissed', 'closed_superseded')", name=op.f("ck_trip_disputes_status_values")),
        sa.CheckConstraint("char_length(btrim(reason)) BETWEEN 1 AND 500", name=op.f("ck_trip_disputes_reason_length")),
        sa.CheckConstraint("resolution_note IS NULL OR char_length(resolution_note) <= 500", name=op.f("ck_trip_disputes_note_length")),
        sa.CheckConstraint("(status = 'open') = (decided_at IS NULL)", name=op.f("ck_trip_disputes_decided_when_closed")),
        sa.CheckConstraint("status NOT IN ('upheld', 'dismissed') OR (resolution_note IS NOT NULL AND resolved_by IS NOT NULL)", name=op.f("ck_trip_disputes_resolution_recorded")),
        sa.ForeignKeyConstraint(["expense_id", "trip_id"], ["trip_expenses.id", "trip_expenses.trip_id"], name=op.f("fk_trip_disputes_expense"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["version_id", "trip_id"], ["trip_expense_versions.id", "trip_expense_versions.trip_id"], name=op.f("fk_trip_disputes_version"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trip_id", "raised_by"], ["trip_members.trip_id", "trip_members.user_id"], name=op.f("fk_trip_disputes_raiser"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], name=op.f("fk_trip_disputes_resolved_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trip_disputes")),
    )  # fmt: skip
    op.create_index(
        "uq_trip_disputes_one_open_per_person", "trip_disputes", ["version_id", "raised_by"],
        unique=True, postgresql_where=sa.text("status = 'open'"),
    )  # fmt: skip
    op.create_index("ix_trip_disputes_trip_id_status", "trip_disputes", ["trip_id", "status"])
    op.execute(
        "CREATE TRIGGER trg_trip_disputes_transition BEFORE UPDATE OR DELETE ON trip_disputes "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_dispute_transition()"
    )


def downgrade() -> None:
    # Test databases only: the upgrade path is forward.
    op.drop_table("trip_disputes")
    op.drop_table("settlements")
    op.execute(
        "DROP FUNCTION finpilot_dispute_transition(); DROP FUNCTION finpilot_settlement_transition();"
    )
