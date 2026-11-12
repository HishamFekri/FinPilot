"""Home dashboard layout: `users.home_layout`.

Additive. One nullable column: the person's own arrangement of Home's widgets (two columns and the
widgets they hid). Null means the default layout. A preference only; no financial data.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("home_layout", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        op.f("ck_users_home_layout_object"),
        "users",
        "home_layout IS NULL OR (jsonb_typeof(home_layout) = 'object' "
        "AND pg_column_size(home_layout) <= 4096)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_home_layout_object"), "users", type_="check")
    op.drop_column("users", "home_layout")
