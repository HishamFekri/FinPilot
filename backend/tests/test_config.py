from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import (
    assert_connected_to_test_database,
    assert_safe_test_database,
    make_settings,
    redact,
)


def test_validation_errors_never_echo_a_password_from_settings() -> None:
    with pytest.raises(ValidationError) as error:
        make_settings(database_url="postgresql://u:s3cret-value@localhost/db")
    assert "s3cret-value" not in str(error.value)


def test_test_settings_ignore_the_environment_and_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's real Google client in the environment or backend/.env must never reach tests.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "123-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "GOCSPX-not-a-real-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
    monkeypatch.setenv("WEB_ORIGIN", "http://localhost:9999")
    settings = make_settings()
    assert not settings.google_configured
    assert settings.google_client_secret is None
    assert settings.web_origin == "http://localhost:3000"


def test_report_redaction_replaces_every_known_secret() -> None:
    message = "url = 'postgresql://u:first@h/d' and conninfo password=second"
    assert (
        redact(message, {"first", "second"})
        == "url = 'postgresql://u:***@h/d' and conninfo password=***"
    )


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pw@localhost/db",  # would select a different driver
        "mysql://user:pw@localhost/db",
        "sqlite:///local.db",
        "not a url",
    ],
)
def test_rejects_unsupported_database_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url=url)


def test_rejects_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        make_settings(app_env="staging")


def test_rejects_invalid_pool_size() -> None:
    with pytest.raises(ValidationError):
        make_settings(db_pool_size=0)


def test_password_is_not_in_settings_repr() -> None:
    settings = make_settings(database_url="postgresql+psycopg://u:s3cret-value@localhost/db_test")
    assert "s3cret-value" not in repr(settings)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@localhost:5432/finpilot_dev",
        "postgresql+psycopg://u:p@localhost:5432/postgres",
        "postgresql+psycopg://u:p@db.example.com:5432/finpilot_test",
    ],
)
def test_test_database_guard_refuses_unsafe_targets(url: str) -> None:
    with pytest.raises(ValueError, match="Refusing"):
        assert_safe_test_database(url)


def test_test_database_guard_accepts_local_test_database() -> None:
    assert_safe_test_database("postgresql+psycopg://u:p@localhost:5432/finpilot_test")


def test_test_database_guard_messages_never_contain_credentials() -> None:
    with pytest.raises(ValueError, match="Refusing") as error:
        assert_safe_test_database("postgresql+psycopg://u:s3cret-value@localhost/finpilot_dev")
    assert "s3cret-value" not in str(error.value)


def test_connected_database_guard_trusts_the_server_not_the_url() -> None:
    # A URL ending in _test that really connects elsewhere, for example through a proxy.
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar_one.return_value = "finpilot_dev"  # noqa: E501
    with pytest.raises(ValueError, match="Refusing to run"):
        assert_connected_to_test_database(engine)


def test_settings_load_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/finpilot_dev")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.log_level == "WARNING"
    assert settings.database_url.path == "/finpilot_dev"


def test_unknown_settings_in_env_file_are_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://u:p@localhost/db\nTEST_DATABASE_URL=ignored\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.database_url.path == "/db"
