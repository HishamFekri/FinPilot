"""Identity schema for Google-only sign-in. Rules: docs/DATABASE.md §3.2.

Google's `sub` is the identity. Email is a copy for display and contact, and identifies nobody.

Users are never hard-deleted (erasure keeps a tombstone row), so every foreign key to `users` is
RESTRICT, and the ORM is told to leave enforcement to the database.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from sqlalchemy import (
    CheckConstraint,
    FetchedValue,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

UuidPk = Annotated[
    uuid.UUID,
    mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
]
CreatedAt = Annotated[datetime, mapped_column(server_default=func.now())]
# Maintained by a database trigger, so raw SQL can't skip it. See migration 0001.
UpdatedAt = Annotated[
    datetime, mapped_column(server_default=func.now(), server_onupdate=FetchedValue())
]
UserFk = Annotated[uuid.UUID, mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))]

SHA256_BYTES = 32
USER_STATUSES = ("active", "suspended", "deletion_pending", "deleted")
LOCALES = ("en", "ar", "tr")


def _sha256_check(column: str) -> str:
    return f"octet_length({column}) = {SHA256_BYTES}"


class User(Base):
    """One person's profile. Holds no credentials."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "email ~ '^[^@[:space:]]+@[^@[:space:]]+$' AND char_length(email) <= 254",
            name="email_format",
        ),
        CheckConstraint("email IS NOT NULL OR status = 'deleted'", name="email_required"),
        CheckConstraint("cardinality(equivalent_targets) <= 12", name="equivalent_targets_bounded"),
        CheckConstraint(
            "display_name IS NULL OR char_length(btrim(display_name)) BETWEEN 1 AND 100",
            name="display_name_length",
        ),
        CheckConstraint(
            "display_name IS NOT NULL OR status = 'deleted'", name="display_name_required"
        ),
        CheckConstraint(
            "status IN ('active', 'suspended', 'deletion_pending', 'deleted')",
            name="status_values",
        ),
        CheckConstraint(
            "status <> 'deletion_pending' OR deletion_requested_at IS NOT NULL",
            name="deletion_pending_has_date",
        ),
        CheckConstraint("locale IN ('en', 'ar', 'tr')", name="locale_values"),
        CheckConstraint(
            """preferred_currency COLLATE "C" ~ '^[A-Z]{3}$'""", name="preferred_currency_format"
        ),
        CheckConstraint(
            """time_zone COLLATE "C" ~ '^[A-Za-z0-9_+/-]{1,64}$'""", name="time_zone_format"
        ),
        CheckConstraint(
            "(terms_version IS NULL) = (terms_accepted_at IS NULL)", name="terms_recorded_together"
        ),
        # Quick Add's starting account: one of the user's own accounts.
        ForeignKeyConstraint(
            ["default_account_id", "id"],
            ["financial_accounts.id", "financial_accounts.user_id"],
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    id: Mapped[UuidPk]
    # Google's last verified address. Not unique, and never an identifier.
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    locale: Mapped[str] = mapped_column(Text, server_default="en")
    # Becomes a foreign key when `currencies` arrives in A3. Null until the user chooses.
    preferred_currency: Mapped[str | None] = mapped_column(String(3))
    time_zone: Mapped[str] = mapped_column(Text, server_default="UTC")
    terms_version: Mapped[str | None] = mapped_column(Text)
    terms_accepted_at: Mapped[datetime | None]
    deletion_requested_at: Mapped[datetime | None]
    last_active_at: Mapped[datetime | None]
    # A4: Quick Add's starting account, and when the one-time setup (default accounts and
    # categories) ran. Setup never runs twice.
    default_account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    setup_completed_at: Mapped[datetime | None]
    # M5: Amount Equivalent favourites, as target keys (USD, XAU_G, BTC...). Display only.
    equivalent_targets: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{USD,EUR,XAU_G,BTC}'::text[]")
    )
    # 0017: the person's arrangement of Home's widgets (null: the default). A preference only.
    home_layout: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    version: Mapped[int] = mapped_column(server_default="1")
    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    identities: Mapped[list["UserIdentity"]] = relationship(
        back_populates="user", passive_deletes="all"
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", passive_deletes="all"
    )


class OAuthState(Base):
    """One in-progress Google sign-in. Single use, short-lived, and bound to the starting browser.

    Only hashes of the state and the browser-binding cookie are kept. The nonce and PKCE verifier
    are needed in clear to finish the flow, and are useless after `expires_at`.
    """

    __tablename__ = "oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash"),
        CheckConstraint(_sha256_check("state_hash"), name="state_hash_is_sha256"),
        CheckConstraint(
            _sha256_check("browser_binding_hash"), name="browser_binding_hash_is_sha256"
        ),
        CheckConstraint("char_length(nonce) BETWEEN 32 AND 128", name="nonce_length"),
        # RFC 7636 allows 43 to 128 characters.
        CheckConstraint(
            "char_length(pkce_verifier) BETWEEN 43 AND 128", name="pkce_verifier_length"
        ),
        CheckConstraint("locale IN ('en', 'ar', 'tr')", name="locale_values"),
        CheckConstraint("expires_at > created_at", name="expiry_order"),
        # A path on FinPilot's own site, never a URL. Checked again by the application.
        CheckConstraint(
            """return_path COLLATE "C" ~ '^/[A-Za-z0-9._~/-]{0,199}$' """
            """AND return_path NOT LIKE '//%'""",
            name="return_path_is_local",
        ),
        Index("ix_oauth_states_expires_at", "expires_at"),
    )

    id: Mapped[UuidPk]
    state_hash: Mapped[bytes] = mapped_column(LargeBinary)
    browser_binding_hash: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[str] = mapped_column(Text)
    pkce_verifier: Mapped[str] = mapped_column(Text)
    # The language to give a new account, chosen before sign-in.
    locale: Mapped[str] = mapped_column(Text, server_default="en")
    return_path: Mapped[str] = mapped_column(Text, server_default="/")
    created_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]


# Mirrors google_oidc.safe_picture_url: https on Google's image host, nothing else.
PICTURE_URL_CHECK = (
    "provider_picture_url IS NULL OR ("
    "char_length(provider_picture_url) <= 2048 AND "
    """provider_picture_url COLLATE "C" ~ '^https://[a-z0-9-]+[.]googleusercontent[.]com/[!-~]*$')"""
)


class UserIdentity(Base):
    """The Google account behind a user. Google's subject ID is the identity, never the email."""

    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject"),
        UniqueConstraint("user_id", "provider"),
        CheckConstraint("provider IN ('google')", name="provider_values"),
        CheckConstraint(
            "char_length(provider_subject) BETWEEN 1 AND 255", name="provider_subject_length"
        ),
        CheckConstraint(PICTURE_URL_CHECK, name="provider_picture_url_format"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    provider: Mapped[str] = mapped_column(Text)
    provider_subject: Mapped[str] = mapped_column(Text)
    # What the provider reported. For display and audit only; never used to authenticate.
    provider_email: Mapped[str | None] = mapped_column(Text)
    provider_email_verified: Mapped[bool] = mapped_column(server_default=text("false"))
    # The profile picture Google reported at the last sign-in, refreshed every time. Display only;
    # Google may retire the URL at any time, so clients fall back to initials.
    provider_picture_url: Mapped[str | None] = mapped_column(Text)
    linked_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(back_populates="identities")


class UserSession(Base):
    """A revocable server-side session.

    Only SHA-256 hashes are stored: of the session token, and for web, of the CSRF token. The
    `csrf_secret` column holds that hash; the raw CSRF token lives only in the browser's cookie.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint(_sha256_check("token_hash"), name="token_hash_is_sha256"),
        CheckConstraint("client_type IN ('web', 'mobile')", name="client_type_values"),
        CheckConstraint(
            "(client_type = 'web') = (csrf_secret IS NOT NULL)", name="csrf_secret_for_web_only"
        ),
        CheckConstraint(
            "idle_expires_at <= absolute_expires_at AND absolute_expires_at > created_at",
            name="expiry_order",
        ),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="revoked_order"),
        Index("ix_sessions_user_id_live", "user_id", postgresql_where=text("revoked_at IS NULL")),
        Index("ix_sessions_absolute_expires_at", "absolute_expires_at"),
    )

    id: Mapped[UuidPk]
    user_id: Mapped[UserFk]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    csrf_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_type: Mapped[str] = mapped_column(Text)
    device_label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    idle_expires_at: Mapped[datetime]
    absolute_expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(back_populates="sessions")


class AuditEvent(Base):
    """The approved `audit_events` table. Trip-scoped events (Phase B) are a trip's activity feed.

    Append-only, enforced by a trigger. Metadata must never hold credentials or raw tokens.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("scope IN ('user', 'trip', 'system')", name="scope_values"),
        CheckConstraint("(scope = 'trip') = (trip_id IS NOT NULL)", name="scope_trip"),
        CheckConstraint("(scope = 'user') = (owner_user_id IS NOT NULL)", name="scope_owner"),
        # A code like auth.session.revoked, so free text can't carry personal data in here.
        CheckConstraint("""action COLLATE "C" ~ '^[a-z][a-z0-9_.]{0,99}$'""", name="action_format"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_is_object"),
        Index(
            "ix_audit_events_owner_user_id_occurred_at",
            "owner_user_id",
            text("occurred_at DESC"),
            postgresql_where=text("scope = 'user'"),
        ),
        Index("ix_audit_events_entity_type_entity_id", "entity_type", "entity_id"),
        Index(
            "ix_audit_events_trip_id_occurred_at",
            "trip_id",
            text("occurred_at DESC"),
            postgresql_where=text("scope = 'trip'"),
        ),
    )

    id: Mapped[UuidPk]
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    scope: Mapped[str] = mapped_column(Text)
    # `metadata` is reserved on declarative classes, so only the attribute name differs.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    request_id: Mapped[str | None] = mapped_column(Text)
    # Set for `trip` scope only (a trip's activity feed); never for a user's private events.
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("trips.id", ondelete="RESTRICT")
    )
