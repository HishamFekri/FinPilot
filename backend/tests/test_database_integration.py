"""Needs a real, dedicated PostgreSQL database. Skipped, visibly, if TEST_DATABASE_URL is unset.

Everything destructive here goes through `test_engine`, which refuses any database that the
server doesn't report as ending in _test.
"""

from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.db import session as session_module
from app.db.session import build_engine, get_db, get_engine
from app.main import create_app
from app.services.readiness import is_ready
from tests.conftest import (
    alembic_config,
    assert_connected_to_test_database,
    make_settings,
    reset_test_schema,
)

pytestmark = pytest.mark.db

PROBE_TABLE = "foundation_probe"
PROBE_MIGRATION = """
from alembic import op
import sqlalchemy as sa

revision = "probe0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("migration_probe", sa.Column("id", sa.Integer, primary_key=True))


def downgrade():
    op.drop_table("migration_probe")
"""


@pytest.fixture
def clean_migrations(test_engine: Engine) -> Iterator[None]:
    reset_test_schema(test_engine)
    yield
    reset_test_schema(test_engine)


@pytest.fixture
def probe_table(test_engine: Engine) -> Iterator[str]:
    assert_connected_to_test_database(test_engine)
    with test_engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
        connection.execute(text(f"CREATE TABLE {PROBE_TABLE} (value TEXT NOT NULL)"))
    yield PROBE_TABLE
    with test_engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))


def _probe_values(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.execute(text(f"SELECT value FROM {PROBE_TABLE}")).scalars())


def _use_real_sessions(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_session_factory", lambda: factory)


def test_database_connection_works(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_connected_database_is_the_test_database(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT current_database()")).scalar_one().endswith("_test")


def test_readiness_endpoint_passes_with_a_working_database(
    test_engine: Engine, clean_migrations: None, test_database_url: str
) -> None:
    command.upgrade(alembic_config(test_database_url), "head")
    assert is_ready(test_engine)

    app = create_app(make_settings(database_url=test_database_url))
    app.dependency_overrides[get_engine] = lambda: test_engine
    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_fails_when_migrations_do_not_match_this_build(
    test_engine: Engine, clean_migrations: None, test_database_url: str, tmp_path: Path
) -> None:
    (tmp_path / "probe0001_probe.py").write_text(PROBE_MIGRATION, encoding="utf-8")
    command.upgrade(alembic_config(test_database_url, tmp_path), "head")
    # The database is at probe0001, which this build's migration directory doesn't contain.
    assert not is_ready(test_engine)


def test_migration_lifecycle_up_and_down(
    test_database_url: str, test_engine: Engine, clean_migrations: None, tmp_path: Path
) -> None:
    """Exercises Alembic with a throwaway revision, so the repository holds no empty migration."""
    (tmp_path / "probe0001_probe.py").write_text(PROBE_MIGRATION, encoding="utf-8")
    config = alembic_config(test_database_url, tmp_path)

    command.upgrade(config, "head")
    assert inspect(test_engine).has_table("migration_probe")
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "probe0001"
        )

    command.downgrade(config, "base")
    assert not inspect(test_engine).has_table("migration_probe")
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 0


def test_request_session_commits_on_success(
    monkeypatch: pytest.MonkeyPatch, test_engine: Engine, probe_table: str
) -> None:
    _use_real_sessions(monkeypatch, test_engine)
    dependency = get_db()
    session = next(dependency)
    session.execute(text(f"INSERT INTO {probe_table} VALUES ('committed')"))
    assert _probe_values(test_engine) == []  # Not visible before the commit.
    with pytest.raises(StopIteration):
        next(dependency)
    assert _probe_values(test_engine) == ["committed"]


def test_request_session_rolls_back_on_error(
    monkeypatch: pytest.MonkeyPatch, test_engine: Engine, probe_table: str
) -> None:
    _use_real_sessions(monkeypatch, test_engine)
    dependency = get_db()
    session = next(dependency)
    session.execute(text(f"INSERT INTO {probe_table} VALUES ('rolled back')"))
    with pytest.raises(RuntimeError, match="boom"):
        dependency.throw(RuntimeError("boom"))
    assert _probe_values(test_engine) == []


def test_connections_are_acquired_released_and_cleaned_up(test_database_url: str) -> None:
    engine = build_engine(make_settings(database_url=test_database_url, db_pool_size=2))
    pool = engine.pool
    try:
        assert_connected_to_test_database(engine)
        with engine.connect() as first, engine.connect() as second:
            assert pool.checkedout() == 2  # type: ignore[attr-defined]
            assert first.execute(text("SELECT 1")).scalar_one() == 1
            assert second.execute(text("SELECT 1")).scalar_one() == 1
        assert pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        engine.dispose()
    assert pool.checkedout() == 0  # type: ignore[attr-defined]


def test_session_returns_its_connection_to_the_pool(
    monkeypatch: pytest.MonkeyPatch, test_database_url: str
) -> None:
    engine = build_engine(make_settings(database_url=test_database_url))
    _use_real_sessions(monkeypatch, engine)
    try:
        dependency = get_db()
        session: Session = next(dependency)
        session.execute(text("SELECT 1"))
        assert engine.pool.checkedout() == 1  # type: ignore[attr-defined]
        with pytest.raises(StopIteration):
            next(dependency)
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        engine.dispose()


def test_pool_exhaustion_times_out_rather_than_hanging(test_database_url: str) -> None:
    engine = build_engine(
        make_settings(
            database_url=test_database_url,
            db_pool_size=1,
            db_max_overflow=0,
            db_pool_timeout_seconds=1,
        )
    )
    try:
        with engine.connect(), pytest.raises(Exception, match="QueuePool limit"):
            engine.connect()
    finally:
        engine.dispose()


def test_statement_timeout_comes_from_settings(test_database_url: str) -> None:
    engine = build_engine(
        make_settings(database_url=test_database_url, db_statement_timeout_ms=150)
    )
    try:
        with engine.connect() as connection, pytest.raises(OperationalError):
            connection.execute(text("SELECT pg_sleep(2)"))
    finally:
        engine.dispose()


def _provisioned_role_only(engine: Engine) -> None:
    """CI's throwaway container uses a superuser, so these only apply to the provisioned role."""
    with engine.connect() as connection:
        role: str = connection.execute(text("SELECT current_user")).scalar_one()
    if role != "finpilot_test_user":
        pytest.skip(f"Applies to the provisioned local role, not {role!r}")


def test_role_has_no_server_wide_privileges(test_engine: Engine) -> None:
    _provisioned_role_only(test_engine)
    with test_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    assert not any(row)


def test_test_role_cannot_connect_to_the_development_database(
    test_engine: Engine, test_database_url: str
) -> None:
    _provisioned_role_only(test_engine)
    url = make_url(test_database_url).set(database="finpilot_dev")
    engine = create_engine(url, poolclass=NullPool)
    with pytest.raises(OperationalError, match="permission denied"), engine.connect():
        pass


def test_test_role_can_only_create_objects_in_its_own_database(test_engine: Engine) -> None:
    _provisioned_role_only(test_engine)
    with test_engine.connect() as connection:
        allowed: Iterable[str] = connection.execute(
            text(
                "SELECT datname FROM pg_database "
                "WHERE has_database_privilege(current_user, oid, 'CREATE') AND NOT datistemplate"
            )
        ).scalars()
        assert list(allowed) == ["finpilot_test"]
