"""The database follows the settings the app was created with, never a copy from the environment.

Regression (CI, commit 834a7fc): get_db built its engine from get_settings(), the environment
and backend/.env. On a machine without them, every request taking a database session raised
ValidationError before the route ran, so Google sign-in answered error=unreachable instead of
google_unavailable, and rate limits never got to respond.
"""

import pytest
from fastapi.testclient import TestClient

from app.db import session as db_session
from app.main import create_app
from tests.conftest import UNREACHABLE_URL, make_settings


def test_the_engine_uses_the_apps_own_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-database-url")  # what the environment says is wrong
    create_app(make_settings(database_url=UNREACHABLE_URL))
    engine = db_session.get_engine()
    assert engine.url.render_as_string(hide_password=False) == UNREACHABLE_URL


def test_sign_in_without_google_or_environment_says_google_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-database-url")
    app = create_app(make_settings())
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert response.status_code == 303
    assert "error=google_unavailable" in response.headers["location"]


def test_a_new_app_replaces_the_previous_apps_engine() -> None:
    create_app(make_settings(database_url=UNREACHABLE_URL))
    first = db_session.get_engine()
    other = UNREACHABLE_URL.replace("/finpilot_test", "/finpilot_other_test")
    create_app(make_settings(database_url=other))
    assert db_session.get_engine() is not first
    assert db_session.get_engine().url.database == "finpilot_other_test"
