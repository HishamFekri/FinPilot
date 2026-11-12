"""Production settings that only matter once the API runs behind a proxy, in a container.

Behind the proxy, every request arrives from the proxy's address. These tests show the visitor's
own address is used for rate limits, but only when the proxy vouches for it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import BACKEND_DIR, Environment, Settings, read_secret
from app.main import create_app
from tests.conftest import make_settings
from tests.google_fakes import CLIENT_ID, CLIENT_SECRET

PROXY = "172.30.10.2"
START = "/api/v1/auth/google/start"  # 20 a client in 10 minutes, counted before anything else
PRODUCTION = {
    "app_env": Environment.PRODUCTION,
    "google_client_id": CLIENT_ID,
    "google_client_secret": CLIENT_SECRET,
    "google_redirect_uri": "https://finpilot.example/api/v1/auth/google/callback",
    "web_origin": "https://finpilot.example",
}


# --- Which proxy is trusted


def test_production_refuses_to_start_without_naming_its_proxy() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXIES"):
        make_settings(**PRODUCTION)
    assert make_settings(**PRODUCTION, trusted_proxies=PROXY).trusted_proxy_list == [PROXY]


@pytest.mark.parametrize("value", ["*", "0.0.0.0/0", "::/0", "10.0.0.0/4", "proxy.local", "1.2.3"])
def test_a_catch_all_or_unreadable_proxy_list_is_refused(value: str) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXIES"):
        make_settings(trusted_proxies=value)


def test_several_proxies_and_a_network_are_accepted() -> None:
    settings = make_settings(trusted_proxies=f" {PROXY}, 172.30.10.0/28 ,")
    assert settings.trusted_proxy_list == [PROXY, "172.30.10.0/28"]


def test_a_blank_pdf_channel_means_the_bundled_browser() -> None:
    """The image sets PDF_BROWSER_CHANNEL= to use Playwright's own Chromium, not system Chrome."""
    assert make_settings(pdf_browser_channel="").pdf_browser_channel is None


# --- The visitor's address behind the proxy


def _client(peer: str, trusted: str | None = PROXY) -> TestClient:
    app = create_app(make_settings(trusted_proxies=trusted))
    return TestClient(
        app, client=(peer, 40000), raise_server_exceptions=False, follow_redirects=False
    )


def _limited(client: TestClient, forwarded_for: str) -> bool:
    """Start is a page load, so a limit arrives as a redirect with error=failed. With Google not
    configured, an allowed request ends in error=google_unavailable instead."""
    response = client.get(START, headers={"X-Forwarded-For": forwarded_for})
    assert response.status_code == 303
    location = response.headers["location"]
    assert "error=failed" in location or "error=google_unavailable" in location
    return "error=failed" in location


def _exhaust(client: TestClient, forwarded_for: str) -> None:
    for _ in range(20):
        assert not _limited(client, forwarded_for)


def test_behind_the_proxy_each_visitor_has_their_own_limit() -> None:
    with _client(PROXY) as client:
        _exhaust(client, "203.0.113.7")
        assert _limited(client, "203.0.113.7")
        # Someone else, through the same proxy, isn't affected.
        assert not _limited(client, "198.51.100.9")


def test_a_client_that_isnt_the_proxy_cant_choose_its_address() -> None:
    """Changing X-Forwarded-For on each request buys nothing: the peer's own address counts."""
    with _client("198.51.100.50") as client:
        for n in range(20):
            assert not _limited(client, f"192.0.2.{n}")
        assert _limited(client, "192.0.2.200")


def test_the_proxy_cant_be_tricked_by_an_address_the_visitor_prepended() -> None:
    """The proxy appends the real address. Only the last untrusted entry counts."""
    with _client(PROXY) as client:
        _exhaust(client, "192.0.2.1, 203.0.113.8")
        assert _limited(client, "192.0.2.99, 203.0.113.8")


def test_without_a_trusted_proxy_forwarded_headers_are_ignored() -> None:
    with _client("198.51.100.60", trusted=None) as client:
        for n in range(20):
            assert not _limited(client, f"192.0.2.{n}")
        assert _limited(client, "192.0.2.201")


# --- Secrets from files


def test_a_secret_comes_from_its_variable_a_named_file_or_the_secrets_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("EXAMPLE_SECRET", "EXAMPLE_SECRET_FILE", "FINPILOT_SECRETS_DIR"):
        monkeypatch.delenv(name, raising=False)
    assert read_secret("EXAMPLE_SECRET") is None

    (tmp_path / "example_secret").write_text("from-the-folder\n", encoding="utf-8")
    monkeypatch.setenv("FINPILOT_SECRETS_DIR", str(tmp_path))
    assert read_secret("EXAMPLE_SECRET") == "from-the-folder"

    named = tmp_path / "elsewhere"
    named.write_text("from-a-named-file", encoding="utf-8")
    monkeypatch.setenv("EXAMPLE_SECRET_FILE", str(named))
    assert read_secret("EXAMPLE_SECRET") == "from-a-named-file"

    monkeypatch.setenv("EXAMPLE_SECRET", "from-the-variable")
    assert read_secret("EXAMPLE_SECRET") == "from-the-variable"

    monkeypatch.setenv("EXAMPLE_SECRET", "  ")
    named.write_text("   \n", encoding="utf-8")
    assert read_secret("EXAMPLE_SECRET") is None  # blank everywhere it was looked for


def test_settings_read_the_database_url_and_google_secret_from_secret_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("DATABASE_URL", "GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_ID", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    url = "postgresql+psycopg://finpilot_app:file-only@finpilot-db:5432/finpilot"
    (tmp_path / "database_url").write_text(url, encoding="utf-8")
    (tmp_path / "google_client_secret").write_text("google-file-only", encoding="utf-8")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        _secrets_dir=tmp_path,
        google_client_id=CLIENT_ID,
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
    )
    assert settings.database_url.hosts()[0]["host"] == "finpilot-db"
    assert settings.google_client_secret is not None
    assert settings.google_client_secret.get_secret_value() == "google-file-only"
    assert "file-only" not in repr(settings)


# --- Migrations use their own credentials


def _alembic_sql(**env: str) -> subprocess.CompletedProcess[str]:
    """Offline mode prints SQL and never connects, so no database is needed or touched."""
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in ("MIGRATION_DATABASE_URL", "DATABASE_URL", "APP_ENV", "FINPILOT_SECRETS_DIR")
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0001", "--sql"],
        cwd=BACKEND_DIR,
        env={**clean, "PYTHONUTF8": "1", **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_production_migrations_never_fall_back_to_the_apps_own_role() -> None:
    result = _alembic_sql(
        APP_ENV="production",
        DATABASE_URL="postgresql+psycopg://finpilot_app:runtime-pw@127.0.0.1:9/finpilot",
    )
    assert result.returncode != 0
    assert "MIGRATION_DATABASE_URL" in result.stderr
    assert "runtime-pw" not in result.stdout + result.stderr


def test_migrations_run_with_only_the_owners_url_and_print_no_secret() -> None:
    result = _alembic_sql(
        APP_ENV="production",
        MIGRATION_DATABASE_URL="postgresql+psycopg://finpilot_owner:owner-pw@127.0.0.1:9/finpilot",
    )
    assert result.returncode == 0, result.stderr[-1500:]
    assert "CREATE TABLE users" in result.stdout
    assert "owner-pw" not in result.stdout + result.stderr


def test_a_migration_url_with_the_wrong_driver_is_refused() -> None:
    result = _alembic_sql(MIGRATION_DATABASE_URL="postgresql://finpilot_owner:pw@127.0.0.1:9/x")
    assert result.returncode != 0 and "postgresql+psycopg" in result.stderr
