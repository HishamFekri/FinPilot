"""Shared fixtures. Unit tests never need a database server, or the developer's .env file."""

import os
from collections.abc import Generator, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
from alembic.config import Config
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from sqlalchemy import Engine, create_engine, inspect, make_url, text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from tests.google_fakes import FakeGoogle

from app.core import clock
from app.core.config import BACKEND_DIR, Environment, Settings
from app.db import hardening
from app.main import create_app
from tests.session_clock import SESSION_NOW

# Port 1 refuses connections, so a unit test can't reach a real database by accident.
JOURNAL_TABLES = ("journal_entries", "journal_lines")
UNREACHABLE_URL = "postgresql+psycopg://test:test@127.0.0.1:1/finpilot_test"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def known_database_passwords() -> set[str]:
    file_values = dotenv_values(BACKEND_DIR / ".env")
    found: set[str] = set()
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_RUNTIME_DATABASE_URL"):
        value = os.environ.get(key) or file_values.get(key)
        if not value:
            continue
        try:
            password = make_url(value).password
        except ArgumentError:
            continue
        if password:
            found.add(str(password))
    return found


def redact(text_value: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        text_value = text_value.replace(secret, "***")
    return text_value


# The app's calendar clock is pinned to the run's one instant (tests/session_clock.py). CI once
# collected the tests at 23:5x UTC and ran them after midnight, so the app dated new records a day
# after the reports' TODAY-bounded ranges.


@pytest.fixture(autouse=True, scope="session")
def _one_calendar_day_per_run() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clock, "now", lambda: SESSION_NOW)
        yield


def redact_longrepr(longrepr: object, secrets: Iterable[str]) -> object:
    """A report's longrepr with every known password removed, in a form pytest still accepts.

    A skip keeps pytest's (path, line, reason) tuple: the terminal summary folds skips by it and
    asserts that shape, so turning it into a string crashed the run whenever a test skipped while
    a password was known (CI's superuser database skips the provisioned-role checks). Strings stay
    strings, and tracebacks render to text, a supported form, then get scrubbed.
    """
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        path, line, reason = longrepr
        return (redact(str(path), secrets), line, redact(str(reason), secrets))
    return redact(str(longrepr), secrets)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(call: pytest.CallInfo[None]) -> Generator[None, object, object]:
    """Tracebacks print function arguments, which can include passwords. Scrub every report."""
    report = yield
    secrets = known_database_passwords()
    if secrets and isinstance(report, pytest.TestReport):
        if report.longrepr is not None:
            report.longrepr = redact_longrepr(report.longrepr, secrets)  # type: ignore[assignment]
        report.sections = [(name, redact(body, secrets)) for name, body in report.sections]
    return report


class IsolatedSettings(Settings):
    """Settings built only from the values a test passes.

    pydantic-settings merges the environment and backend/.env even into model_validate, so
    without this a developer's real Google credentials leaked into tests (found in A2.4).
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_settings(**overrides: object) -> Settings:
    return IsolatedSettings.model_validate(
        {
            "app_env": Environment.TEST,
            "database_url": UNREACHABLE_URL,
            "db_connect_timeout_seconds": 1,
            **overrides,
        }
    )


@pytest.fixture
def app() -> FastAPI:
    return create_app(make_settings())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False lets tests see the 500 response a real client would get.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def assert_safe_test_database(url: str) -> None:
    """First guard, before connecting. Messages name the database and host, never credentials."""
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not name.endswith("_test"):
        raise ValueError(f"Refusing to use database {name!r}: its name must end in _test")
    if parts.hostname not in LOCAL_HOSTS:
        raise ValueError(f"Refusing to use host {parts.hostname!r}: tests only run locally")


def assert_connected_to_test_database(engine: Engine) -> None:
    """Second guard, after connecting: trust what the server reports, not the URL."""
    with engine.connect() as connection:
        name: str = connection.execute(text("SELECT current_database()")).scalar_one()
    if not name.endswith("_test"):
        raise ValueError(f"Refusing to run: connected to {name!r}, not a test database")


def alembic_config(database_url: str, versions_dir: Path | None = None) -> Config:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # ConfigParser treats % as interpolation, and encoded passwords can contain it.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    if versions_dir is not None:
        config.set_main_option("version_locations", str(versions_dir))
    return config


def reset_test_schema(engine: Engine) -> None:
    """Drops every table and FinPilot function, only after the server confirms a _test database."""
    assert_connected_to_test_database(engine)
    with engine.begin() as connection:
        for table in inspect(connection).get_table_names():
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
        functions: Iterable[str] = connection.execute(
            text(
                "SELECT p.oid::regprocedure::text FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname LIKE 'finpilot%'"
            )
        ).scalars()
        for function in list(functions):
            connection.execute(text(f"DROP FUNCTION {function} CASCADE"))


def runtime_database_url() -> str | None:
    """Set only by `tests.pg_cluster`: the app then connects as the restricted runtime role."""
    return os.environ.get("TEST_RUNTIME_DATABASE_URL")


def harden_if_runtime(engine: Engine) -> None:
    """After migrating: grants and row-level security, when running as the runtime role."""
    if runtime_database_url():
        with engine.connect() as connection:
            name = connection.scalar(text("SELECT current_database()"))
        hardening.apply_hardening(engine, str(name), os.environ["TEST_RUNTIME_ROLE"])


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get(
        "TEST_DATABASE_URL"
    )
    if not url:
        pytest.skip("TEST_DATABASE_URL isn't set, so no database test can run")
    assert_safe_test_database(url)
    return url


@pytest.fixture
def test_engine(test_database_url: str) -> Iterator[Engine]:
    engine = create_engine(test_database_url, poolclass=NullPool)
    assert_connected_to_test_database(engine)
    yield engine
    engine.dispose()


# --- Signing in through the real routes, with Google replaced at the HTTP layer (tests only)


@pytest.fixture(scope="module")
def auth_engine(test_database_url: str) -> Iterator[Engine]:
    """A migrated test database for one module, emptied between tests by `auth_clean`."""
    from alembic import command

    engine = create_engine(test_database_url, poolclass=NullPool)
    reset_test_schema(engine)
    command.upgrade(alembic_config(test_database_url), "head")
    harden_if_runtime(engine)
    yield engine
    reset_test_schema(engine)
    engine.dispose()


@pytest.fixture
def auth_clean(auth_engine: Engine) -> Iterator[None]:
    yield
    assert_connected_to_test_database(auth_engine)
    with auth_engine.begin() as connection:
        # The journal refuses TRUNCATE (migration 0005), and the cascade from users reaches it.
        # Only this cleanup, on a confirmed _test database, lifts that for one statement.
        for table in JOURNAL_TABLES:
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER trg_{table}_no_truncate"))
        connection.execute(text("TRUNCATE users, audit_events, oauth_states CASCADE"))
        for table in JOURNAL_TABLES:
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER trg_{table}_no_truncate"))


@pytest.fixture
def factory(auth_engine: Engine, auth_clean: None) -> "sessionmaker[Session]":
    return sessionmaker(bind=auth_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def google() -> "FakeGoogle":
    from tests.google_fakes import FakeGoogle

    return FakeGoogle()


@pytest.fixture
def auth_app(
    monkeypatch: pytest.MonkeyPatch,
    test_database_url: str,
    factory: "sessionmaker[Session]",
    google: "FakeGoogle",
) -> FastAPI:
    from app.api.v1.auth import get_google_provider
    from app.db import session as session_module
    from tests.google_fakes import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI

    runtime = runtime_database_url()
    app_factory = factory
    if runtime:
        app_factory = sessionmaker(
            bind=create_engine(runtime, poolclass=NullPool),
            autoflush=False,
            expire_on_commit=False,
        )
    monkeypatch.setattr(session_module, "_session_factory", lambda: app_factory)
    app = create_app(
        make_settings(
            database_url=test_database_url,
            google_client_id=CLIENT_ID,
            google_client_secret=CLIENT_SECRET,
            google_redirect_uri=REDIRECT_URI,
        )
    )
    # The real client class, pointed at the fake Google. Only tests do this.
    fake_client = google.client()
    app.dependency_overrides[get_google_provider] = lambda: fake_client
    return app


@pytest.fixture
def browser(auth_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(auth_app, raise_server_exceptions=False) as client:
        yield client
