"""Migration 0003, the approved Google-only cleanup, on the test database only."""

from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from app.services.readiness import expected_migration_heads
from tests.conftest import alembic_config, assert_connected_to_test_database, reset_test_schema

pytestmark = pytest.mark.db

DROPPED_TABLES = {"password_credentials", "identity_conflicts", "auth_tokens"}


@pytest.fixture
def engine(test_database_url: str) -> Iterator[Engine]:
    engine = create_engine(test_database_url, poolclass=NullPool)
    reset_test_schema(engine)
    yield engine
    reset_test_schema(engine)
    engine.dispose()


def test_0003_is_part_of_the_real_history() -> None:
    assert expected_migration_heads() == {"0017"}


def _user(engine: Engine, email: str, status: str = "active") -> None:
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (email, display_name, status) VALUES (:e, 'U', :s)"),
            {"e": email, "s": status},
        )


def test_0003_drops_password_era_objects_and_keeps_users(
    engine: Engine, test_database_url: str
) -> None:
    assert_connected_to_test_database(engine)
    config = alembic_config(test_database_url)
    command.upgrade(config, "0002")
    _user(engine, "kept@example.com")
    command.upgrade(config, "0003")

    tables = set(inspect(engine).get_table_names())
    assert not tables & DROPPED_TABLES
    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    assert not {"email_normalized", "email_verified_at"} & columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1


def test_0003_refuses_to_run_if_obsolete_tables_hold_data(
    engine: Engine, test_database_url: str
) -> None:
    config = alembic_config(test_database_url)
    command.upgrade(config, "0002")
    _user(engine, "has-password@example.com")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO password_credentials (user_id, password_hash) "
                "SELECT id, '$argon2id$v=19$m=19456,t=2,p=1$c2FsdA$aGFzaA' FROM users"
            )
        )
    with pytest.raises(DBAPIError, match="Migration 0003 refused"):
        command.upgrade(config, "0003")
    assert "password_credentials" in inspect(engine).get_table_names()


def test_0003_refuses_to_run_if_a_user_is_quarantined(
    engine: Engine, test_database_url: str
) -> None:
    config = alembic_config(test_database_url)
    command.upgrade(config, "0002")
    _user(engine, "q@example.com", status="quarantined")
    with pytest.raises(DBAPIError, match="Migration 0003 refused"):
        command.upgrade(config, "0003")


def test_0003_and_0004_downgrade_back_to_the_earlier_shape(
    engine: Engine, test_database_url: str
) -> None:
    config = alembic_config(test_database_url)
    command.upgrade(config, "0002")
    with engine.connect() as connection:
        before = _shape(connection)
    command.upgrade(config, "head")
    command.downgrade(config, "0002")
    with engine.connect() as connection:
        assert _shape(connection) == before


def _shape(connection: object) -> dict[str, object]:
    inspector = inspect(connection)
    return {
        table: (
            sorted(c["name"] for c in inspector.get_columns(table)),
            sorted(str(x["name"]) for x in inspector.get_check_constraints(table)),
            sorted(str(x["name"]) for x in inspector.get_indexes(table)),
        )
        for table in inspector.get_table_names()
    }
