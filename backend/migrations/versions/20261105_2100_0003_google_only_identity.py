"""Simplify the identity schema for Google-only sign-in. Approved by the owner for A2.3.

Applied to development only after read-only checks confirmed the dropped tables were empty.

Destructive operations:
- DROP TABLE auth_tokens, identity_conflicts, password_credentials
- DROP COLUMN users.email_normalized (generated) and users.email_verified_at
- DROP INDEX uq_users_email_normalized_claim: email stops being unique, because Google says an
  address can change and be reassigned, while `sub` is never reused
- Replaces the users status and email checks: no `quarantined`, and non-ASCII addresses allowed,
  because email no longer identifies anyone

The upgrade refuses to run if any dropped table holds a row, or any user is quarantined.

The downgrade restores the structure, not the data. It fails if two users now share an email, or an
address is non-ASCII, because the old constraints can't hold then.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFUSE_IF_DATA = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM auth_tokens)
        OR EXISTS (SELECT 1 FROM identity_conflicts)
        OR EXISTS (SELECT 1 FROM password_credentials)
        OR EXISTS (SELECT 1 FROM users WHERE status = 'quarantined')
    THEN
        RAISE EXCEPTION 'Migration 0003 refused: obsolete identity tables hold data';
    END IF;
END
$$
"""
OLD_STATUSES = "'active', 'suspended', 'quarantined', 'deletion_pending', 'deleted'"
NEW_STATUSES = "'active', 'suspended', 'deletion_pending', 'deleted'"
OLD_EMAIL_CHECK = """email COLLATE "C" ~ '^[!-?A-~]+@[!-?A-~]+$' AND char_length(email) <= 254"""
NEW_EMAIL_CHECK = """email ~ '^[^@[:space:]]+@[^@[:space:]]+$' AND char_length(email) <= 254"""


def upgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.drop_table("auth_tokens")
    op.drop_table("identity_conflicts")
    op.drop_table("password_credentials")

    op.drop_index("uq_users_email_normalized_claim", table_name="users")
    op.drop_constraint(op.f("ck_users_quarantined_is_unverified"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_status_values"), "users", type_="check")
    op.create_check_constraint(
        op.f("ck_users_status_values"), "users", f"status IN ({NEW_STATUSES})"
    )
    op.drop_constraint(op.f("ck_users_email_format"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_email_format"), "users", NEW_EMAIL_CHECK)
    op.drop_column("users", "email_normalized")
    op.drop_column("users", "email_verified_at")


def downgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.TIMESTAMP(timezone=True)))
    op.add_column(
        "users",
        # STORED explicitly: PostgreSQL 18 defaults to virtual, which can't be indexed.
        sa.Column(
            "email_normalized", sa.Text(), sa.Computed('lower(email COLLATE "C")', persisted=True)
        ),
    )
    op.drop_constraint(op.f("ck_users_email_format"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_email_format"), "users", OLD_EMAIL_CHECK)
    op.drop_constraint(op.f("ck_users_status_values"), "users", type_="check")
    op.create_check_constraint(
        op.f("ck_users_status_values"), "users", f"status IN ({OLD_STATUSES})"
    )
    op.create_check_constraint(
        op.f("ck_users_quarantined_is_unverified"),
        "users",
        "status <> 'quarantined' OR email_verified_at IS NULL",
    )
    op.create_index(
        "uq_users_email_normalized_claim",
        "users",
        ["email_normalized"],
        unique=True,
        postgresql_where=sa.text("status <> 'quarantined'"),
    )

    timestamp = sa.TIMESTAMP(timezone=True)
    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("password_changed_at", timestamp, server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "password_hash LIKE '$argon2id$%'",
            name=op.f("ck_password_credentials_password_hash_is_argon2id"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_password_credentials_status_values")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_credentials_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_password_credentials")),
    )
    op.execute(
        "CREATE TRIGGER trg_password_credentials_set_updated_at BEFORE UPDATE ON "
        "password_credentials FOR EACH ROW EXECUTE FUNCTION finpilot_set_updated_at()"
    )
    op.create_table(
        "identity_conflicts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_subject", sa.Text(), nullable=False),
        sa.Column("provider_email", sa.Text(), nullable=False),
        sa.Column("existing_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("resolved_at", timestamp, nullable=True),
        sa.Column("resolved_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name=op.f("ck_identity_conflicts_resolved_at_state"),
        ),
        sa.CheckConstraint(
            """
            CASE status
                WHEN 'resolved_separate_account' THEN
                    resolved_user_id IS NOT NULL AND resolved_user_id <> existing_user_id
                WHEN 'resolved_linked' THEN resolved_user_id = existing_user_id
                ELSE resolved_user_id IS NULL
            END
            """,
            name=op.f("ck_identity_conflicts_resolved_user_matches_outcome"),
        ),
        sa.CheckConstraint(
            "provider IN ('google')", name=op.f("ck_identity_conflicts_provider_values")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved_separate_account', 'resolved_linked', 'cancelled')",
            name=op.f("ck_identity_conflicts_status_values"),
        ),
        sa.CheckConstraint(
            "char_length(provider_subject) BETWEEN 1 AND 255",
            name=op.f("ck_identity_conflicts_provider_subject_length"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_identity_conflicts_expiry_order")
        ),
        sa.ForeignKeyConstraint(
            ["existing_user_id"],
            ["users.id"],
            name=op.f("fk_identity_conflicts_existing_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_user_id"],
            ["users.id"],
            name=op.f("fk_identity_conflicts_resolved_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_conflicts")),
    )
    op.create_index(
        "ix_identity_conflicts_existing_user_id", "identity_conflicts", ["existing_user_id"]
    )
    op.create_index(
        "uq_identity_conflicts_pending_subject",
        "identity_conflicts",
        ["provider", "provider_subject"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("new_email", sa.Text(), nullable=True),
        sa.Column("identity_conflict_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", timestamp, server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("consumed_at", timestamp, nullable=True),
        sa.Column("invalidated_at", timestamp, nullable=True),
        sa.CheckConstraint(
            "(purpose = 'change_email') = (new_email IS NOT NULL)",
            name=op.f("ck_auth_tokens_new_email"),
        ),
        sa.CheckConstraint(
            "(purpose = 'resolve_identity_conflict') = (identity_conflict_id IS NOT NULL)",
            name=op.f("ck_auth_tokens_identity_conflict_purpose"),
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password', 'change_email', "
            "'resolve_identity_conflict')",
            name=op.f("ck_auth_tokens_purpose_values"),
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR invalidated_at IS NULL",
            name=op.f("ck_auth_tokens_consumed_or_invalidated"),
        ),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_auth_tokens_expiry_order")),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_auth_tokens_token_hash_is_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["identity_conflict_id"],
            ["identity_conflicts.id"],
            name=op.f("fk_auth_tokens_identity_conflict_id_identity_conflicts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_tokens_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_tokens_token_hash")),
    )
    op.create_index("ix_auth_tokens_expires_at", "auth_tokens", ["expires_at"])
    op.create_index(
        "uq_auth_tokens_one_open_per_purpose",
        "auth_tokens",
        ["user_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND invalidated_at IS NULL"),
    )
