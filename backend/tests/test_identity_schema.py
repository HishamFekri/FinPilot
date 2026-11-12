"""Identity constraints for Google-only sign-in, against the real migrations, on the test database.

Each test runs inside a transaction that's rolled back. Only the migration tests commit, and they
go through `reset_test_schema`, which asks the server for the database name first.
"""

import uuid
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models import AuditEvent, OAuthState, User, UserIdentity, UserSession
from tests.conftest import alembic_config, assert_connected_to_test_database, reset_test_schema

pytestmark = pytest.mark.db

IDENTITY_TABLES = {"users", "user_identities", "sessions", "oauth_states", "audit_events"}
OBSOLETE_TABLES = {"password_credentials", "identity_conflicts", "auth_tokens"}
LATER = datetime.now(UTC) + timedelta(days=1)


@pytest.fixture(scope="module")
def schema_engine(test_database_url: str) -> Iterator[Engine]:
    engine = create_engine(test_database_url, poolclass=NullPool)
    reset_test_schema(engine)
    command.upgrade(alembic_config(test_database_url), "head")
    yield engine
    reset_test_schema(engine)
    engine.dispose()


@pytest.fixture
def db(schema_engine: Engine) -> Iterator[Session]:
    """Each flush that fails only undoes itself, and the whole test is rolled back at the end."""
    with schema_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


def _hash(value: str) -> bytes:
    return sha256(value.encode()).digest()


def _user(db: Session, email: str = "a@example.com", **fields: Any) -> User:
    user = User(email=email, display_name="Alex", **fields)
    db.add(user)
    db.flush()
    return user


def _identity(db: Session, user: User, subject: str = "sub-1") -> UserIdentity:
    identity = UserIdentity(
        user_id=user.id, provider="google", provider_subject=subject, provider_email=user.email
    )
    db.add(identity)
    db.flush()
    return identity


def _session(db: Session, user: User, token: str = "t", **fields: Any) -> UserSession:
    values: dict[str, Any] = {
        "client_type": "mobile",
        "idle_expires_at": LATER,
        "absolute_expires_at": LATER,
        **fields,
    }
    row = UserSession(user_id=user.id, token_hash=_hash(token), **values)
    db.add(row)
    db.flush()
    return row


def _rejected(db: Session, constraint: str, action: Callable[[], object]) -> None:
    """The action must fail on this named constraint, without ending the test's transaction."""
    with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
        action()
        db.flush()


def _violates(db: Session, constraint: str, sql: str, **parameters: Any) -> None:
    """Raw SQL, proving the database rejects this with no help from the ORM."""
    with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
        db.execute(text(sql), parameters)


# --- Users


def test_creating_a_user_applies_safe_defaults(db: Session) -> None:
    user = _user(db, "Person@Example.com")
    db.refresh(user)
    assert isinstance(user.id, uuid.UUID)
    assert user.email == "Person@Example.com"  # Stored as Google reported it.
    assert (user.status, user.locale, user.time_zone) == ("active", "en", "UTC")
    assert user.preferred_currency is None
    assert user.version == 1


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("email", None, "ck_users_email_required"),
        ("display_name", None, "ck_users_display_name_required"),
        ("display_name", "   ", "ck_users_display_name_length"),
        ("status", "banana", "ck_users_status_values"),
        ("status", "quarantined", "ck_users_status_values"),
        ("locale", "fr", "ck_users_locale_values"),
        ("preferred_currency", "eu", "ck_users_preferred_currency_format"),
        ("time_zone", "Not A Zone!", "ck_users_time_zone_format"),
    ],
)
def test_user_fields_are_required_or_constrained(
    db: Session, column: str, value: str | None, constraint: str
) -> None:
    fields = {"email": "a@example.com", "display_name": "Alex", column: value}
    _violates(
        db,
        constraint,
        "INSERT INTO users (email, display_name, status, locale, preferred_currency, time_zone) "
        "VALUES (:email, :display_name, :status, :locale, :preferred_currency, :time_zone)",
        **{
            "status": "active",
            "locale": "en",
            "preferred_currency": None,
            "time_zone": "UTC",
            **fields,
        },
    )


def test_erased_tombstones_may_have_no_email_or_name(db: Session) -> None:
    for _ in range(2):
        db.execute(
            text("INSERT INTO users (email, display_name, status) VALUES (NULL, NULL, 'deleted')")
        )


@pytest.mark.parametrize(
    "email",
    [
        "no-at-sign",
        "two@@example.com",
        "has space@example.com",
        "@example.com",
        "a@",
        "x" * 250 + "@example.com",
    ],
)
def test_malformed_emails_are_refused(db: Session, email: str) -> None:
    _violates(
        db,
        "ck_users_email_format",
        "INSERT INTO users (email, display_name) VALUES (:email, 'Alex')",
        email=email,
    )


def test_emails_identify_nobody_so_they_may_repeat_and_need_not_be_ascii(db: Session) -> None:
    """Google can reassign an address, and Workspace domains can be internationalized."""
    first, second = _user(db, "shared@example.com"), _user(db, "shared@example.com")
    assert first.id != second.id
    _user(db, "ali@şirket.com.tr")


def test_obsolete_password_era_objects_are_gone(schema_engine: Engine) -> None:
    inspector = inspect(schema_engine)
    assert not OBSOLETE_TABLES & set(inspector.get_table_names())
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert not {"email_normalized", "email_verified_at"} & columns
    assert "uq_users_email_normalized_claim" not in {
        i["name"] for i in inspector.get_indexes("users")
    }


# --- Google identities


def test_a_google_subject_can_belong_to_only_one_user(db: Session) -> None:
    _identity(db, _user(db, "a@example.com"), "shared-sub")
    second = _user(db, "b@example.com")
    _rejected(
        db,
        "uq_user_identities_provider_provider_subject",
        lambda: _identity(db, second, "shared-sub"),
    )


def test_a_user_has_one_google_identity(db: Session) -> None:
    user = _user(db)
    identity = _identity(db, user, "sub-1")
    db.refresh(user)
    assert user.identities == [identity]
    _rejected(db, "uq_user_identities_user_id_provider", lambda: _identity(db, user, "sub-2"))


def test_unknown_provider_and_empty_subject_are_refused(db: Session) -> None:
    user = _user(db)
    insert = (
        "INSERT INTO user_identities (user_id, provider, provider_subject) "
        "VALUES (:id, :provider, :subject)"
    )
    _violates(
        db, "ck_user_identities_provider_values", insert, id=user.id, provider="x", subject="s"
    )
    _violates(
        db,
        "ck_user_identities_provider_subject_length",
        insert,
        id=user.id,
        provider="google",
        subject="",
    )


def test_an_identity_needs_a_real_user(db: Session) -> None:
    _violates(
        db,
        "fk_user_identities_user_id_users",
        "INSERT INTO user_identities (user_id, provider, provider_subject) "
        "VALUES (:id, 'google', 's')",
        id=uuid.uuid4(),
    )


# --- Sessions


def test_session_needs_a_real_owner(db: Session) -> None:
    _violates(
        db,
        "fk_sessions_user_id_users",
        "INSERT INTO sessions (user_id, token_hash, client_type, idle_expires_at, "
        "absolute_expires_at) VALUES (:id, :hash, 'mobile', :later, :later)",
        id=uuid.uuid4(),
        hash=_hash("x"),
        later=LATER,
    )


def test_session_token_hash_is_unique_and_must_be_a_hash(db: Session) -> None:
    user = _user(db)
    _session(db, user, "same")
    _rejected(db, "uq_sessions_token_hash", lambda: _session(db, user, "same"))
    _rejected(
        db,
        "ck_sessions_token_hash_is_sha256",
        lambda: db.add(
            UserSession(
                user_id=user.id,
                token_hash=b"a-raw-url-safe-session-token-of-43-characters",
                client_type="mobile",
                idle_expires_at=LATER,
                absolute_expires_at=LATER,
            )
        ),
    )


def test_web_sessions_need_a_csrf_hash_and_mobile_sessions_have_none(db: Session) -> None:
    user = _user(db)
    _rejected(
        db,
        "ck_sessions_csrf_secret_for_web_only",
        lambda: _session(db, user, "w", client_type="web"),
    )
    _rejected(
        db,
        "ck_sessions_csrf_secret_for_web_only",
        lambda: _session(db, user, "m", client_type="mobile", csrf_secret=b"s"),
    )


def test_session_time_order_is_enforced(db: Session) -> None:
    user = _user(db)
    past = datetime.now(UTC) - timedelta(days=1)
    _rejected(
        db,
        "ck_sessions_expiry_order",
        lambda: _session(db, user, "a", idle_expires_at=LATER + timedelta(days=1)),
    )
    _rejected(db, "ck_sessions_revoked_order", lambda: _session(db, user, "b", revoked_at=past))


# --- Sign-in flows


def _state(db: Session, **fields: Any) -> OAuthState:
    values: dict[str, Any] = {
        "state_hash": _hash("s"),
        "browser_binding_hash": _hash("b"),
        "nonce": "n" * 43,
        "pkce_verifier": "v" * 86,
        "expires_at": LATER,
        **fields,
    }
    row = OAuthState(**values)
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize(
    "path", ["https://evil.example", "//evil.example", "/\\evil", "/en?x=1", "en", "/" + "a" * 200]
)
def test_return_path_must_be_a_local_path(db: Session, path: str) -> None:
    _rejected(db, "ck_oauth_states_return_path_is_local", lambda: _state(db, return_path=path))


def test_a_valid_return_path_is_kept(db: Session) -> None:
    assert _state(db, return_path="/tr/dashboard").return_path == "/tr/dashboard"


# --- Deletion semantics


@pytest.mark.parametrize(
    ("make_child", "constraint"),
    [
        (_identity, "fk_user_identities_user_id_users"),
        (_session, "fk_sessions_user_id_users"),
    ],
)
def test_a_user_with_records_cannot_be_hard_deleted(
    db: Session, make_child: Callable[[Session, User], object], constraint: str
) -> None:
    user = _user(db)
    make_child(db, user)
    _violates(db, constraint, "DELETE FROM users WHERE id = :id", id=user.id)


def test_orm_delete_also_leaves_enforcement_to_the_database(db: Session) -> None:
    user = _user(db)
    _identity(db, user)
    db.refresh(user)
    _rejected(db, "fk_user_identities_user_id_users", lambda: db.delete(user))


# --- Audit


def _audit(db: Session, user: User | None, **fields: Any) -> AuditEvent:
    values: dict[str, Any] = {
        "action": "auth.session.created",
        "scope": "user" if user else "system",
        "owner_user_id": user.id if user else None,
        "actor_user_id": user.id if user else None,
        **fields,
    }
    event = AuditEvent(**values)
    db.add(event)
    db.flush()
    return event


def test_audit_events_are_append_only(db: Session) -> None:
    event = _audit(db, _user(db), event_metadata={"reason": "test"})
    _violates(
        db, "append-only", "UPDATE audit_events SET action = 'tampered' WHERE id = :id", id=event.id
    )
    _violates(db, "append-only", "DELETE FROM audit_events WHERE id = :id", id=event.id)


def test_audit_events_keep_their_user_from_being_hard_deleted(db: Session) -> None:
    user = _user(db)
    _audit(db, user)
    _violates(db, "fk_audit_events", "DELETE FROM users WHERE id = :id", id=user.id)


def test_audit_scope_and_action_format_are_enforced(db: Session) -> None:
    user = _user(db)
    _rejected(db, "ck_audit_events_scope_values", lambda: _audit(db, None, scope="team"))
    # Trip scope (Phase B) needs its trip; a user-scoped event can never carry one.
    _rejected(db, "ck_audit_events_scope_trip", lambda: _audit(db, None, scope="trip"))
    _rejected(db, "ck_audit_events_scope_owner", lambda: _audit(db, user, scope="system"))
    _rejected(
        db, "ck_audit_events_action_format", lambda: _audit(db, user, action="Login a@example.com")
    )


# --- Timestamps and versioning


def test_updated_at_moves_on_raw_sql_updates_and_created_at_does_not(db: Session) -> None:
    user = _user(db)
    db.refresh(user)
    created, updated = user.created_at, user.updated_at
    db.execute(text("UPDATE users SET display_name = 'Sam' WHERE id = :id"), {"id": user.id})
    db.refresh(user)
    assert user.updated_at > updated
    assert user.created_at == created
    assert user.created_at.tzinfo is not None


def test_every_timestamp_column_stores_a_time_zone(schema_engine: Engine) -> None:
    inspector = inspect(schema_engine)
    naive = [
        f"{table}.{column['name']}"
        for table in IDENTITY_TABLES
        for column in inspector.get_columns(table)
        if str(column["type"]) == "TIMESTAMP" and not column["type"].timezone  # type: ignore[attr-defined]
    ]
    assert naive == []


def test_stale_user_writes_are_refused_by_the_version_column(db: Session) -> None:
    user = _user(db)
    db.execute(text("UPDATE users SET version = version + 1 WHERE id = :id"), {"id": user.id})
    user.display_name = "Stale"
    with pytest.raises(StaleDataError), db.begin_nested():
        db.flush()


# --- Migrations, on the test database only


def _schema_objects(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        names: Iterable[str] = connection.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname NOT LIKE 'alembic_version%' "
                "UNION ALL "
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public'"
            )
        ).scalars()
        return set(names)


def test_migrations_upgrade_downgrade_and_upgrade_again(
    schema_engine: Engine, test_database_url: str
) -> None:
    assert_connected_to_test_database(schema_engine)
    config = alembic_config(test_database_url)
    command.downgrade(config, "base")
    assert _schema_objects(schema_engine) == set()
    command.upgrade(config, "head")
    assert set(inspect(schema_engine).get_table_names()) >= IDENTITY_TABLES
    with schema_engine.connect() as connection:
        version: str = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert version == "0017"


def test_migrations_match_the_models_exactly(schema_engine: Engine) -> None:
    with schema_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_users_hold_no_google_tokens(schema_engine: Engine) -> None:
    columns = {
        column["name"]
        for table in IDENTITY_TABLES
        for column in inspect(schema_engine).get_columns(table)
    }
    assert not columns & {"id_token", "access_token", "refresh_token", "password", "password_hash"}
