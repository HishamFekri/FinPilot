"""Trips, members, invitations, and shared expenses with versions and shares (Phase B, B1-B2).

Additive: six new tables, and `audit_events` gains a nullable `trip_id` and the `trip` scope (its
scope check is widened, never narrowed). No existing row changes.

Rules enforced here (DATABASE.md §3.5, FINANCIAL_RULES.md §8, §12):

- A trip has at most one active owner. Membership rows are never deleted.
- The base currency can't change once the trip has an expense.
- An expense's payer is immutable; expenses are never updated or deleted.
- A version's financial content is immutable; only its state and decision columns change, and
  only along the state machine: pending -> approved | rejected | withdrawn, approved -> superseded.
- At most one pending and one approved version per expense (partial unique indexes).
- Shares are immutable, and (checked at commit) sum exactly to the version's amount and base
  amount (INV-09). Cancellation versions have no shares.
- Trip categories are the shared default expense keys (categories became per-user in A4, so a
  trip can't point at one member's category row).

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_MINOR = 10**15
EXPENSE_KEYS = (
    "food_and_drinks", "groceries", "transportation", "shopping", "housing", "bills_utilities",
    "health", "education", "entertainment", "travel", "personal_care", "subscriptions", "gifts",
    "other_expense",
)  # fmt: skip
KEYS_SQL = ", ".join(f"'{k}'" for k in EXPENSE_KEYS)

FUNCTIONS = """
CREATE FUNCTION finpilot_trip_base_currency_locked() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.base_currency IS DISTINCT FROM OLD.base_currency
       AND EXISTS (SELECT 1 FROM trip_expenses WHERE trip_id = OLD.id) THEN
        RAISE EXCEPTION 'trip base currency is locked once an expense exists'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION finpilot_block_trip_record_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = 'check_violation';
END $$;

CREATE FUNCTION finpilot_trip_version_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'expense versions are never deleted' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.expense_id, NEW.trip_id, NEW.version_no, NEW.kind, NEW.proposed_by,
        NEW.description, NEW.notes, NEW.category_key, NEW.expense_date, NEW.amount_minor,
        NEW.currency, NEW.base_currency, NEW.base_amount_minor, NEW.rate_id, NEW.split_method,
        NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.expense_id, OLD.trip_id, OLD.version_no, OLD.kind, OLD.proposed_by,
        OLD.description, OLD.notes, OLD.category_key, OLD.expense_date, OLD.amount_minor,
        OLD.currency, OLD.base_currency, OLD.base_amount_minor, OLD.rate_id, OLD.split_method,
        OLD.created_at) THEN
        RAISE EXCEPTION 'an expense version''s content is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
        (OLD.state = 'pending' AND NEW.state IN ('approved', 'rejected', 'withdrawn'))
        OR (OLD.state = 'approved' AND NEW.state = 'superseded')
    ) THEN
        RAISE EXCEPTION 'expense version can''t go from % to %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION finpilot_check_version_shares() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target uuid;
    v_kind text;
    v_amount bigint;
    v_base bigint;
    total bigint;
    base_total bigint;
    n integer;
BEGIN
    -- Each branch only touches the columns of the table that fired the trigger.
    IF TG_TABLE_NAME = 'expense_shares' THEN
        target := NEW.version_id;
    ELSE
        target := NEW.id;
    END IF;
    SELECT kind, amount_minor, base_amount_minor INTO v_kind, v_amount, v_base
      FROM trip_expense_versions WHERE id = target;
    SELECT coalesce(sum(share_minor), 0), coalesce(sum(base_share_minor), 0), count(*)
      INTO total, base_total, n
      FROM expense_shares WHERE version_id = target;
    IF v_kind = 'cancellation' THEN
        IF n > 0 THEN
            RAISE EXCEPTION 'a cancellation has no shares' USING ERRCODE = 'check_violation';
        END IF;
    ELSIF total <> v_amount OR base_total <> v_base THEN
        RAISE EXCEPTION 'shares of version % don''t sum to its amount', target
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;
"""


def upgrade() -> None:
    op.execute(FUNCTIONS)
    now = sa.text("now()")
    ts = sa.TIMESTAMP(timezone=True)

    op.create_table(
        "trips",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("destination", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("base_currency", sa.CHAR(3), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", ts, server_default=now, nullable=False),
        sa.Column("updated_at", ts, server_default=now, nullable=False),
        sa.CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 100", name=op.f("ck_trips_name_length")),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 500", name=op.f("ck_trips_description_length")),
        sa.CheckConstraint("destination IS NULL OR char_length(destination) <= 100", name=op.f("ck_trips_destination_length")),
        sa.CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name=op.f("ck_trips_dates_ordered")),
        sa.CheckConstraint("status IN ('active', 'completed', 'archived')", name=op.f("ck_trips_status_values")),
        sa.ForeignKeyConstraint(["base_currency"], ["currencies.code"], name=op.f("fk_trips_base_currency_currencies"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_trips_created_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trips")),
    )  # fmt: skip
    op.execute(
        "CREATE TRIGGER trg_trips_set_updated_at BEFORE UPDATE ON trips "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
    )
    op.execute(
        "CREATE TRIGGER trg_trips_base_currency_locked BEFORE UPDATE ON trips "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_trip_base_currency_locked()"
    )

    op.create_table(
        "trip_members",
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("joined_at", ts, server_default=now, nullable=False),
        sa.Column("ended_at", ts, nullable=True),
        sa.Column("ended_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name=op.f("ck_trip_members_role_values")),
        sa.CheckConstraint("status IN ('active', 'left', 'removed')", name=op.f("ck_trip_members_status_values")),
        sa.CheckConstraint("(status = 'active') = (ended_at IS NULL)", name=op.f("ck_trip_members_ended_when_inactive")),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], name=op.f("fk_trip_members_trip_id_trips"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_trip_members_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ended_by"], ["users.id"], name=op.f("fk_trip_members_ended_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("trip_id", "user_id", name=op.f("pk_trip_members")),
    )  # fmt: skip
    op.create_index(
        "uq_trip_members_one_active_owner", "trip_members", ["trip_id"], unique=True,
        postgresql_where=sa.text("role = 'owner' AND status = 'active'"),
    )  # fmt: skip
    op.create_index("ix_trip_members_user_id_status", "trip_members", ["user_id", "status"])

    op.create_table(
        "trip_invitations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", BYTEA(), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("invitee_label", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("expires_at", ts, nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", ts, nullable=True),
        sa.Column("created_at", ts, server_default=now, nullable=False),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'declined', 'revoked')", name=op.f("ck_trip_invitations_status_values")),
        sa.CheckConstraint("invitee_label IS NULL OR char_length(invitee_label) <= 60", name=op.f("ck_trip_invitations_label_length")),
        sa.CheckConstraint("octet_length(token_hash) = 32", name=op.f("ck_trip_invitations_hash_length")),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], name=op.f("fk_trip_invitations_trip_id_trips"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], name=op.f("fk_trip_invitations_invited_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_trip_invitations_decided_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trip_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_trip_invitations_token_hash")),
    )  # fmt: skip
    op.create_index("ix_trip_invitations_trip_id_status", "trip_invitations", ["trip_id", "status"])

    op.create_table(
        "trip_expenses",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("payer_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("replaces_expense_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", ts, server_default=now, nullable=False),
        sa.ForeignKeyConstraint(["trip_id", "payer_user_id"], ["trip_members.trip_id", "trip_members.user_id"], name=op.f("fk_trip_expenses_payer_trip_members"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trip_id", "created_by"], ["trip_members.trip_id", "trip_members.user_id"], name=op.f("fk_trip_expenses_creator_trip_members"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["replaces_expense_id", "trip_id"], ["trip_expenses.id", "trip_expenses.trip_id"], name=op.f("fk_trip_expenses_replaces_trip_expenses"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trip_expenses")),
        sa.UniqueConstraint("id", "trip_id", name=op.f("uq_trip_expenses_id_trip_id")),
    )  # fmt: skip
    op.create_index(
        "ix_trip_expenses_trip_id_created_at", "trip_expenses",
        ["trip_id", sa.literal_column("created_at DESC")],
    )  # fmt: skip
    op.execute(
        "CREATE TRIGGER trg_trip_expenses_immutable BEFORE UPDATE OR DELETE ON trip_expenses "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_block_trip_record_change()"
    )

    op.create_table(
        "trip_expense_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("expense_id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("category_key", sa.Text(), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("base_currency", sa.CHAR(3), nullable=False),
        sa.Column("base_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("rate_id", sa.Uuid(), nullable=True),
        sa.Column("split_method", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", ts, nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", ts, server_default=now, nullable=False),
        sa.CheckConstraint("kind IN ('initial', 'correction', 'cancellation')", name=op.f("ck_trip_expense_versions_kind_values")),
        sa.CheckConstraint("state IN ('pending', 'approved', 'rejected', 'withdrawn', 'superseded')", name=op.f("ck_trip_expense_versions_state_values")),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_trip_expense_versions_version_positive")),
        sa.CheckConstraint("(kind = 'initial') = (version_no = 1)", name=op.f("ck_trip_expense_versions_initial_first")),
        sa.CheckConstraint("char_length(btrim(description)) BETWEEN 1 AND 120", name=op.f("ck_trip_expense_versions_description_length")),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 500", name=op.f("ck_trip_expense_versions_notes_length")),
        sa.CheckConstraint("decision_reason IS NULL OR char_length(decision_reason) <= 500", name=op.f("ck_trip_expense_versions_reason_length")),
        sa.CheckConstraint(f"category_key IN ({KEYS_SQL})", name=op.f("ck_trip_expense_versions_category_key")),
        sa.CheckConstraint(f"amount_minor > 0 AND amount_minor <= {MAX_MINOR}", name=op.f("ck_trip_expense_versions_amount_range")),
        sa.CheckConstraint(f"base_amount_minor > 0 AND base_amount_minor <= {MAX_MINOR}", name=op.f("ck_trip_expense_versions_base_amount_range")),
        sa.CheckConstraint("(currency = base_currency) = (rate_id IS NULL)", name=op.f("ck_trip_expense_versions_rate_when_converted")),
        sa.CheckConstraint("currency <> base_currency OR amount_minor = base_amount_minor", name=op.f("ck_trip_expense_versions_same_currency_same_amount")),
        sa.CheckConstraint("split_method IN ('equal', 'fixed', 'percentage')", name=op.f("ck_trip_expense_versions_split_method_values")),
        sa.CheckConstraint("(state IN ('pending')) = (decided_at IS NULL)", name=op.f("ck_trip_expense_versions_decided_when_closed")),
        sa.ForeignKeyConstraint(["expense_id", "trip_id"], ["trip_expenses.id", "trip_expenses.trip_id"], name=op.f("fk_trip_expense_versions_expense"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposed_by"], ["users.id"], name=op.f("fk_trip_expense_versions_proposed_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_trip_expense_versions_decided_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["currency"], ["currencies.code"], name=op.f("fk_trip_expense_versions_currency_currencies"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_currency"], ["currencies.code"], name=op.f("fk_trip_expense_versions_base_currency_currencies"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rate_id"], ["fx_rates.id"], name=op.f("fk_trip_expense_versions_rate_id_fx_rates"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trip_expense_versions")),
        sa.UniqueConstraint("expense_id", "version_no", name=op.f("uq_trip_expense_versions_expense_id_version_no")),
        sa.UniqueConstraint("id", "trip_id", name=op.f("uq_trip_expense_versions_id_trip_id")),
    )  # fmt: skip
    op.create_index(
        "uq_trip_expense_versions_one_pending", "trip_expense_versions", ["expense_id"],
        unique=True, postgresql_where=sa.text("state = 'pending'"),
    )  # fmt: skip
    op.create_index(
        "uq_trip_expense_versions_one_approved", "trip_expense_versions", ["expense_id"],
        unique=True, postgresql_where=sa.text("state = 'approved'"),
    )  # fmt: skip
    op.create_index("ix_trip_expense_versions_trip_id_state", "trip_expense_versions", ["trip_id", "state"])
    op.execute(
        "CREATE TRIGGER trg_trip_expense_versions_transition BEFORE UPDATE OR DELETE "
        "ON trip_expense_versions FOR EACH ROW EXECUTE FUNCTION finpilot_trip_version_transition()"
    )

    op.create_table(
        "expense_shares",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("input_value", sa.Numeric(20, 4), nullable=True),
        sa.Column("share_minor", sa.BigInteger(), nullable=False),
        sa.Column("base_share_minor", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(f"share_minor > 0 AND share_minor <= {MAX_MINOR}", name=op.f("ck_expense_shares_share_range")),
        sa.CheckConstraint(f"base_share_minor >= 0 AND base_share_minor <= {MAX_MINOR}", name=op.f("ck_expense_shares_base_share_range")),
        sa.ForeignKeyConstraint(["version_id", "trip_id"], ["trip_expense_versions.id", "trip_expense_versions.trip_id"], name=op.f("fk_expense_shares_version"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trip_id", "user_id"], ["trip_members.trip_id", "trip_members.user_id"], name=op.f("fk_expense_shares_member"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("version_id", "user_id", name=op.f("pk_expense_shares")),
    )  # fmt: skip
    op.create_index("ix_expense_shares_trip_id_user_id", "expense_shares", ["trip_id", "user_id"])
    op.execute(
        "CREATE TRIGGER trg_expense_shares_immutable BEFORE UPDATE OR DELETE ON expense_shares "
        "FOR EACH ROW EXECUTE FUNCTION finpilot_block_trip_record_change()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_expense_shares_sum AFTER INSERT ON expense_shares "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION finpilot_check_version_shares()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_trip_expense_versions_shares AFTER INSERT "
        "ON trip_expense_versions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION finpilot_check_version_shares()"
    )

    # Trip-scoped audit events: the trip's activity feed (DATABASE.md audit_events).
    op.add_column("audit_events", sa.Column("trip_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_audit_events_trip_id_trips"), "audit_events", "trips", ["trip_id"], ["id"],
        ondelete="RESTRICT",
    )  # fmt: skip
    op.drop_constraint(op.f("ck_audit_events_scope_values"), "audit_events", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_events_scope_values"), "audit_events", "scope IN ('user', 'trip', 'system')"
    )
    op.create_check_constraint(
        op.f("ck_audit_events_scope_trip"), "audit_events", "(scope = 'trip') = (trip_id IS NOT NULL)"
    )
    op.create_index(
        "ix_audit_events_trip_id_occurred_at", "audit_events",
        ["trip_id", sa.literal_column("occurred_at DESC")],
        postgresql_where=sa.text("scope = 'trip'"),
    )  # fmt: skip


def downgrade() -> None:
    # Test databases only: the upgrade path is forward.
    op.drop_index("ix_audit_events_trip_id_occurred_at", table_name="audit_events")
    op.drop_constraint(op.f("ck_audit_events_scope_trip"), "audit_events", type_="check")
    op.drop_constraint(op.f("ck_audit_events_scope_values"), "audit_events", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_events_scope_values"), "audit_events", "scope IN ('user', 'system')"
    )
    op.drop_constraint(op.f("fk_audit_events_trip_id_trips"), "audit_events", type_="foreignkey")
    op.drop_column("audit_events", "trip_id")
    for table in (
        "expense_shares",
        "trip_expense_versions",
        "trip_expenses",
        "trip_invitations",
        "trip_members",
        "trips",
    ):
        op.drop_table(table)
    op.execute(
        "DROP FUNCTION finpilot_check_version_shares(); "
        "DROP FUNCTION finpilot_trip_version_transition(); "
        "DROP FUNCTION finpilot_block_trip_record_change(); "
        "DROP FUNCTION finpilot_trip_base_currency_locked();"
    )
