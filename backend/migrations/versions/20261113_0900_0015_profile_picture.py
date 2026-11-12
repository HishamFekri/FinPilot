"""Google profile picture: `user_identities.provider_picture_url`.

Additive. One nullable column and its check; no existing row changes, and no financial table is
touched.

The ID token's `picture` claim (scope `profile`, already requested) is kept next to the address
Google reported, and refreshed at every sign-in. It is display only, like `provider_email`: an
https URL on Google's image host, never a token. Existing rows stay null until their next sign-in,
and the app shows initials until then.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# https on Google's image host, at most 2048 characters (google_oidc.safe_picture_url).
PICTURE_URL_CHECK = (
    "provider_picture_url IS NULL OR ("
    "char_length(provider_picture_url) <= 2048 AND "
    """provider_picture_url COLLATE "C" ~ '^https://[a-z0-9-]+[.]googleusercontent[.]com/[!-~]*$')"""
)


def upgrade() -> None:
    op.add_column("user_identities", sa.Column("provider_picture_url", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_user_identities_provider_picture_url_format"), "user_identities", PICTURE_URL_CHECK
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_user_identities_provider_picture_url_format"), "user_identities", type_="check"
    )
    op.drop_column("user_identities", "provider_picture_url")
