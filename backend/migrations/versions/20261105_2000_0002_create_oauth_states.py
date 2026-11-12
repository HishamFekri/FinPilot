"""Create oauth_states, for single-use Google sign-in flows. Additive only.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("browser_binding_hash", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("pkce_verifier", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), server_default="en", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(state_hash) = 32", name=op.f("ck_oauth_states_state_hash_is_sha256")
        ),
        sa.CheckConstraint(
            "octet_length(browser_binding_hash) = 32",
            name=op.f("ck_oauth_states_browser_binding_hash_is_sha256"),
        ),
        sa.CheckConstraint(
            "char_length(nonce) BETWEEN 32 AND 128", name=op.f("ck_oauth_states_nonce_length")
        ),
        sa.CheckConstraint(
            "char_length(pkce_verifier) BETWEEN 43 AND 128",
            name=op.f("ck_oauth_states_pkce_verifier_length"),
        ),
        sa.CheckConstraint(
            "locale IN ('en', 'ar', 'tr')", name=op.f("ck_oauth_states_locale_values")
        ),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_oauth_states_expiry_order")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_states")),
        sa.UniqueConstraint("state_hash", name=op.f("uq_oauth_states_state_hash")),
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states")
    op.drop_table("oauth_states")
