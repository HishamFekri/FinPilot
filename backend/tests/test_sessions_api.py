"""Sessions, authorization, CSRF, profile, and rate limits through the real HTTP API.

Users sign in through the real Google routes against a fake Google (MOCKED, tests only).
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.deps import cookie_names
from app.core.config import Environment
from app.db.models import AuditEvent, User, UserSession
from app.services import sessions
from tests.auth_harness import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    WEB_ORIGIN,
    actions,
    count,
    sign_in,
    write_headers,
)
from tests.conftest import make_settings
from tests.google_fakes import CLIENT_ID, CLIENT_SECRET, FakeGoogle

pytestmark = pytest.mark.db

ME = "/api/v1/auth/me"
LOGOUT = "/api/v1/auth/logout"
SESSIONS = "/api/v1/auth/sessions"


def second_browser(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --- Session validation


def test_me_returns_safe_fields_only(browser: TestClient, google: FakeGoogle) -> None:
    sign_in(browser, google)
    response = browser.get(ME)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "id",
        "email",
        "display_name",
        "avatar_url",  # a public image URL on Google's host, or null (test_profile_picture.py)
        "locale",
        "preferred_currency",
        "time_zone",
        "status",
        "created_at",
    }
    assert body["email"] == "alex@example.com"
    assert body["avatar_url"] is None  # no picture in this token
    assert "google-subject-1" not in response.text


def test_every_account_response_forbids_caching(browser: TestClient, google: FakeGoogle) -> None:
    # Found against real Google in A2.4: model-returning routes had no Cache-Control at all.
    csrf = sign_in(browser, google)
    responses = {
        "GET me": browser.get(ME),
        "PATCH me": browser.patch(ME, json={"display_name": "Sam"}, headers=write_headers(csrf)),
        "GET sessions": browser.get(SESSIONS),
        "DELETE other sessions": browser.delete(SESSIONS, headers=write_headers(csrf)),
        "logout": browser.post(LOGOUT, headers=write_headers(csrf)),
    }
    for name, response in responses.items():
        assert response.status_code in {200, 204}, name
        assert response.headers.get("cache-control") == "no-store", name
        assert response.headers.get("referrer-policy") == "no-referrer", name


@pytest.mark.parametrize("token", [None, "", "not-a-real-token", "x" * 500])
def test_missing_or_invalid_sessions_get_401(browser: TestClient, token: str | None) -> None:
    if token is not None:
        browser.cookies.set(SESSION_COOKIE, token)
    response = browser.get(ME)
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_a_bearer_header_is_not_accepted_yet(browser: TestClient, google: FakeGoogle) -> None:
    sign_in(browser, google)
    token = browser.cookies[SESSION_COOKIE]
    browser.cookies.clear()
    assert browser.get(ME, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def _age_session(factory: sessionmaker[Session], **values: Any) -> None:
    with factory.begin() as session:
        session.execute(
            text(
                "UPDATE sessions SET created_at = now() - interval '40 days', "
                "last_seen_at = now() - interval '40 days', "
                "idle_expires_at = :idle, absolute_expires_at = :absolute"
            ),
            values,
        )


@pytest.mark.parametrize("expired", ["idle", "absolute"])
def test_expired_sessions_get_401(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session], expired: str
) -> None:
    sign_in(browser, google)
    past = datetime.now(UTC) - timedelta(minutes=1)
    future = datetime.now(UTC) + timedelta(days=1)
    if expired == "idle":
        _age_session(factory, idle=past, absolute=future)
    else:
        _age_session(factory, idle=past, absolute=past)
    assert browser.get(ME).status_code == 401


def test_use_slides_idle_expiry_but_never_past_the_absolute_limit(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    sign_in(browser, google)
    soon = datetime.now(UTC) + timedelta(hours=1)
    absolute = datetime.now(UTC) + timedelta(hours=2)
    _age_session(factory, idle=soon, absolute=absolute)
    assert browser.get(ME).status_code == 200
    with factory() as session:
        row = session.scalars(select(UserSession)).one()
    assert row.idle_expires_at == row.absolute_expires_at
    assert row.last_seen_at > datetime.now(UTC) - timedelta(minutes=1)


def test_suspended_users_are_refused_and_their_session_ends(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    sign_in(browser, google)
    with factory.begin() as session:
        session.execute(text("UPDATE users SET status = 'suspended'"))
    response = browser.get(ME)
    assert response.status_code == 403
    assert response.json()["code"] == "account_unavailable"
    assert "auth.session.denied" in actions(factory)
    assert browser.get(ME).status_code == 401  # That session was revoked.


def test_deleted_users_sessions_stop_working(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    sign_in(browser, google)
    with factory.begin() as session:
        session.execute(
            text("UPDATE users SET status = 'deleted', email = NULL, display_name = NULL")
        )
    assert browser.get(ME).status_code == 401
    with factory() as session:
        assert session.scalars(select(UserSession.revoked_at)).one() is not None


def test_deletion_pending_users_can_still_use_the_app(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    sign_in(browser, google)
    with factory.begin() as session:
        session.execute(
            text("UPDATE users SET status = 'deletion_pending', deletion_requested_at = now()")
        )
    assert browser.get(ME).status_code == 200


def test_tokens_have_256_bits_and_are_never_stored(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    sign_in(browser, google)
    token = browser.cookies[SESSION_COOKIE]
    assert len(token) >= 43
    with factory() as session:
        raw = str(session.execute(text("SELECT * FROM sessions")).all())
    assert token not in raw
    assert browser.cookies[CSRF_COOKIE] not in raw


# --- Multiple sessions and revocation


def test_several_sessions_and_revoking_one(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    with second_browser(auth_app) as laptop:
        sign_in(laptop, google)
        listed = browser.get(SESSIONS).json()
        assert len(listed) == 2
        assert sum(item["current"] for item in listed) == 1
        other = next(item for item in listed if not item["current"])
        assert "token" not in str(listed).lower()

        response = browser.delete(f"{SESSIONS}/{other['id']}", headers=write_headers(csrf))
        assert response.status_code == 204
        assert laptop.get(ME).status_code == 401
        assert browser.get(ME).status_code == 200
    assert "auth.session.revoked" in actions(factory)


def test_revoking_all_other_sessions_keeps_this_one(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    with second_browser(auth_app) as other:
        sign_in(other, google)
        assert browser.delete(SESSIONS, headers=write_headers(csrf)).status_code == 204
        assert other.get(ME).status_code == 401
    assert browser.get(ME).status_code == 200


def test_revoking_the_current_session_clears_its_cookies(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    own = browser.get(SESSIONS).json()[0]["id"]
    response = browser.delete(f"{SESSIONS}/{own}", headers=write_headers(csrf))
    assert response.status_code == 204
    assert SESSION_COOKIE not in browser.cookies
    assert browser.get(ME).status_code == 401


def test_nobody_can_see_or_revoke_another_users_sessions(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle
) -> None:
    alice_csrf = sign_in(browser, google, subject="alice", email="alice@example.com")
    with second_browser(auth_app) as bob:
        bob_csrf = sign_in(bob, google, subject="bob", email="bob@example.com")
        bob_session = bob.get(SESSIONS).json()[0]["id"]

        assert all(item["id"] != bob_session for item in browser.get(SESSIONS).json())
        response = browser.delete(f"{SESSIONS}/{bob_session}", headers=write_headers(alice_csrf))
        assert response.status_code == 404  # Not "forbidden": its existence isn't revealed.
        assert bob.get(ME).status_code == 200

        # Bob's CSRF token is useless with Alice's session.
        assert (
            browser.patch(
                ME, json={"display_name": "X"}, headers=write_headers(bob_csrf)
            ).status_code
            == 403
        )
    assert (
        browser.delete(f"{SESSIONS}/{uuid.uuid4()}", headers=write_headers(alice_csrf)).status_code
        == 404
    )


def test_a_user_id_in_the_request_never_selects_the_user(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle
) -> None:
    sign_in(browser, google, subject="alice", email="alice@example.com")
    with second_browser(auth_app) as bob:
        csrf = sign_in(bob, google, subject="bob", email="bob@example.com")
        alice_id = browser.get(ME).json()["id"]
        response = bob.patch(
            ME, json={"id": alice_id, "display_name": "Hijack"}, headers=write_headers(csrf)
        )
        assert response.status_code == 422  # Unknown fields are refused outright.
    assert browser.get(ME).json()["display_name"] == "Alex Example"


# --- Logout


def test_logout_revokes_the_session_and_clears_cookies(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    token = browser.cookies[SESSION_COOKIE]
    response = browser.post(LOGOUT, headers=write_headers(csrf))
    assert response.status_code == 204
    assert SESSION_COOKIE not in browser.cookies
    browser.cookies.set(SESSION_COOKIE, token)
    assert browser.get(ME).status_code == 401  # Revoked on the server, not just forgotten.
    assert "auth.logout" in actions(factory)


def test_logout_is_safe_to_repeat(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    assert browser.post(LOGOUT, headers=write_headers(csrf)).status_code == 204
    assert browser.post(LOGOUT, headers=write_headers(csrf)).status_code == 204
    assert browser.post(LOGOUT).status_code == 204


def test_another_site_cannot_log_someone_out(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    assert (
        browser.post(LOGOUT, headers=write_headers(csrf, "https://evil.example")).status_code == 403
    )
    assert browser.post(LOGOUT, headers=write_headers(None)).status_code == 403
    assert browser.get(ME).status_code == 200


# --- CSRF on every change


@pytest.mark.parametrize(
    ("csrf", "origin"),
    [
        ("valid", None),  # No Origin and no Referer.
        ("valid", "https://evil.example"),
        ("valid", "http://localhost:3001"),
        ("valid", "null"),
        (None, WEB_ORIGIN),
        ("wrong", WEB_ORIGIN),
        ("", WEB_ORIGIN),
    ],
)
def test_changes_need_an_allowed_origin_and_the_csrf_token(
    browser: TestClient, google: FakeGoogle, csrf: str | None, origin: str | None
) -> None:
    token = sign_in(browser, google)
    value = token if csrf == "valid" else csrf
    response = browser.patch(ME, json={"display_name": "Sam"}, headers=write_headers(value, origin))
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
    assert browser.get(ME).json()["display_name"] == "Alex Example"


def test_a_same_origin_referer_is_accepted_when_origin_is_absent(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    headers = {"X-CSRF-Token": csrf, "Referer": f"{WEB_ORIGIN}/en/settings"}
    assert browser.patch(ME, json={"display_name": "Sam"}, headers=headers).status_code == 200


def test_session_revocation_needs_csrf(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    own = browser.get(SESSIONS).json()[0]["id"]
    assert browser.delete(f"{SESSIONS}/{own}", headers=write_headers(None)).status_code == 403
    assert (
        browser.delete(SESSIONS, headers=write_headers(csrf, "https://evil.example")).status_code
        == 403
    )
    assert browser.get(ME).status_code == 200


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def _api_routes(routes: Any, prefix: str = "") -> Iterator[tuple[APIRoute, str]]:
    """Every API route with its full path. Included routers nest, so `app.routes` alone is empty."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route, prefix + route.path
        elif hasattr(route, "original_router"):
            base = prefix + (getattr(route.include_context, "prefix", "") or "")
            yield from _api_routes(route.original_router.routes, base)


def test_the_route_walk_really_finds_the_routes(auth_app: FastAPI) -> None:
    """If this fails, the two guards below are checking nothing."""
    found = {path for _, path in _api_routes(auth_app.routes)}
    assert found == set(auth_app.openapi()["paths"])
    assert len(found) > 100


def test_every_state_changing_route_is_csrf_protected(auth_app: FastAPI) -> None:
    """A guard for future routes too: anything that changes data must pass CSRF checks."""
    unprotected = []
    for route, path in _api_routes(auth_app.routes):
        if not (route.methods or set()) & deps.UNSAFE_METHODS:
            continue
        names = _dependency_names(route)
        # Logout checks CSRF itself, because it must also work without a session.
        if "require_csrf" not in names and route.endpoint.__name__ != "logout":
            unprotected.append(path)
    assert unprotected == []


def test_every_route_except_sign_in_and_health_needs_a_session(auth_app: FastAPI) -> None:
    public = {
        "/api/v1/healthz",
        "/api/v1/readyz",
        "/api/v1/auth/google/start",
        "/api/v1/auth/google/callback",
        LOGOUT,
    }
    for route, path in _api_routes(auth_app.routes):
        if path not in public:
            assert "require_session" in _dependency_names(route), path


# --- Profile


def test_profile_updates_are_validated_saved_and_audited(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    csrf = sign_in(browser, google)
    changes = {
        "display_name": "  Sam  ",
        "locale": "tr",
        "preferred_currency": "TRY",
        "time_zone": "Europe/Istanbul",
    }
    response = browser.patch(ME, json=changes, headers=write_headers(csrf))
    assert response.status_code == 200
    body = response.json()
    assert (
        body["display_name"],
        body["locale"],
        body["preferred_currency"],
        body["time_zone"],
    ) == ("Sam", "tr", "TRY", "Europe/Istanbul")
    with factory() as session:
        event = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "user.profile.updated")
        ).one()
    assert event.event_metadata == {"fields": "display_name,locale,preferred_currency,time_zone"}
    assert "Istanbul" not in str(event.event_metadata)  # Field names only, never values.


@pytest.mark.parametrize(
    "changes",
    [
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "x" * 101},
        {"display_name": "Alex‮evil"},
        {"display_name": None},
        {"locale": "fr"},
        {"locale": None},
        {"preferred_currency": "try"},
        {"preferred_currency": "EURO"},
        {"time_zone": "Mars/Olympus"},
        {"time_zone": "UTC; DROP TABLE users"},
        {"email": "new@example.com"},
        {"provider_subject": "someone-else"},
        {"status": "active"},
        {},
    ],
)
def test_invalid_or_forbidden_profile_changes_are_refused(
    browser: TestClient, google: FakeGoogle, changes: dict[str, Any]
) -> None:
    csrf = sign_in(browser, google)
    before = browser.get(ME).json()
    response = browser.patch(ME, json=changes, headers=write_headers(csrf))
    assert response.status_code == 422
    assert browser.get(ME).json() == before


def test_profile_needs_a_session(browser: TestClient) -> None:
    response = browser.patch(ME, json={"display_name": "X"}, headers=write_headers("x"))
    assert response.status_code == 401


def test_currency_and_time_zone_can_be_cleared_or_kept(
    browser: TestClient, google: FakeGoogle
) -> None:
    csrf = sign_in(browser, google)
    browser.patch(ME, json={"preferred_currency": "EUR"}, headers=write_headers(csrf))
    response = browser.patch(ME, json={"preferred_currency": None}, headers=write_headers(csrf))
    assert response.json()["preferred_currency"] is None


# --- Rate limits


def test_sign_in_start_is_rate_limited_per_client(browser: TestClient) -> None:
    statuses = [
        browser.get("/api/v1/auth/google/start", follow_redirects=False).status_code
        for _ in range(21)
    ]
    assert statuses[:20] == [302] * 20
    # A page load: over the limit it ends on the landing page with a message, not raw JSON.
    assert statuses[20] == 303
    last = browser.get("/api/v1/auth/google/start", follow_redirects=False)
    assert last.status_code == 303
    assert last.headers["location"].endswith("/en?error=failed")
    assert "testclient" not in last.headers["location"]  # The client address is never echoed.


def test_writes_are_rate_limited(browser: TestClient, google: FakeGoogle) -> None:
    csrf = sign_in(browser, google)
    statuses = {
        browser.patch(ME, json={"locale": "en"}, headers=write_headers(csrf)).status_code
        for _ in range(61)
    }
    assert statuses == {200, 429}


# --- Configuration and cookies


def test_production_cookies_are_secure_and_host_only() -> None:
    settings = make_settings(
        app_env=Environment.PRODUCTION,
        google_client_id=CLIENT_ID,
        google_client_secret=CLIENT_SECRET,
        google_redirect_uri="https://finpilot.example/api/v1/auth/google/callback",
        web_origin="https://finpilot.example",
        trusted_proxies="172.30.10.2",
        beta_allowlist="owner@example.com",
    )
    assert settings.secure_cookies
    names = cookie_names(settings)
    assert names.session.startswith("__Host-") and names.csrf.startswith("__Host-")


@pytest.mark.parametrize(
    ("env", "overrides", "message"),
    [
        (Environment.PRODUCTION, {"web_origin": "http://finpilot.example"}, "https"),
        (Environment.PRODUCTION, {"web_origin": "https://other.example"}, "same host"),
        (Environment.TEST, {"web_origin": "http://127.0.0.1:3000"}, "same host"),
        (Environment.TEST, {"web_origin": "https://x.example/path"}, "origin only"),
        (Environment.TEST, {"session_idle_hours": 48, "session_absolute_hours": 24}, "exceed"),
    ],
)
def test_unsafe_session_configuration_is_refused(
    env: Environment, overrides: dict[str, Any], message: str
) -> None:
    redirect = (
        "https://finpilot.example/api/v1/auth/google/callback"
        if env is Environment.PRODUCTION
        else "http://localhost:8000/api/v1/auth/google/callback"
    )
    with pytest.raises(ValueError, match=message):
        make_settings(
            app_env=env,
            google_client_id=CLIENT_ID,
            google_client_secret=CLIENT_SECRET,
            google_redirect_uri=redirect,
            **overrides,
        )


# --- Cleanup and data hygiene


def test_long_dead_sessions_are_cleaned_up_when_a_new_one_is_issued(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    sign_in(browser, google)
    with factory.begin() as session:
        session.execute(
            text(
                "UPDATE sessions SET created_at = now() - interval '200 days', "
                "last_seen_at = now() - interval '200 days', "
                "idle_expires_at = now() - interval '100 days', "
                "absolute_expires_at = now() - interval '100 days'"
            )
        )
    browser.cookies.clear()
    sign_in(browser, google)
    assert count(factory, UserSession) == 1


def test_sessions_cannot_outlive_their_user_row(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        user = User(email="a@example.com", display_name="A")
        session.add(user)
        session.flush()
        issued = sessions.issue_session(
            session, user, idle=timedelta(hours=1), absolute=timedelta(hours=2), user_agent=None
        )
        assert issued.token not in repr(issued)
    # Whichever RESTRICT key the database checks first, the user row stays.
    with pytest.raises(Exception, match="violates RESTRICT"), factory.begin() as session:
        session.execute(text("DELETE FROM users"))


def test_no_session_or_csrf_token_reaches_logs_or_audit(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    csrf = sign_in(browser, google)
    token = browser.cookies[SESSION_COOKIE]
    browser.get(ME)
    browser.patch(ME, json={"display_name": "Sam"}, headers=write_headers(csrf))
    browser.post(LOGOUT, headers=write_headers(csrf))
    with factory() as session:
        audit_text = str(session.execute(text("SELECT * FROM audit_events")).all())
    for secret in (token, csrf):
        assert secret not in caplog.text
        assert secret not in audit_text


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        ("Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/130.0 Safari/537.36", "Chrome on Windows"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1", "Safari on iOS"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko Firefox/130.0", "Firefox on Linux"),
        ("curl/8.0", None),
        (None, None),
    ],
)
def test_device_labels_are_coarse(user_agent: str | None, label: str | None) -> None:
    assert sessions.device_label(user_agent) == label
