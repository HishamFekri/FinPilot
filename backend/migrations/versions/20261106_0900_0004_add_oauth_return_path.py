"""Add oauth_states.return_path: where to send the browser after sign-in. Additive only.

Only a path on FinPilot's own site is allowed, never a URL, so sign-in can't become an open
redirect. The application checks the same rule before storing it.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oauth_states",
        sa.Column("return_path", sa.Text(), server_default="/", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_oauth_states_return_path_is_local"),
        "oauth_states",
        """return_path COLLATE "C" ~ '^/[A-Za-z0-9._~/-]{0,199}$' """
        """AND return_path NOT LIKE '//%'""",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_oauth_states_return_path_is_local"), "oauth_states", type_="check")
    op.drop_column("oauth_states", "return_path")
