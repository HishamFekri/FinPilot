"""Google sign-in through the real routes, services, and migrations, on the test database only.

MOCKED: Google is replaced by tests/google_fakes.py at the HTTP layer. Nothing here proves sign-in
works with real Google; that is A2.4.
"""

import logging
import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ApiError
from app.db.models import AuditEvent, OAuthState, User, UserIdentity, UserSession
from app.main import create_app
from app.services import google_signin, sessions
from app.services.google_oidc import GoogleClaims
from tests.auth_harness import (
    BINDING_COOKIE,
    CALLBACK,
    CSRF_COOKIE,
    SESSION_COOKIE,
    START,
    WEB_ORIGIN,
    actions,
    begin,
    count,
    finish,
)
from tests.conftest import make_settings
from tests.google_fakes import CLIENT_SECRET, FakeGoogle, make_id_token

pytestmark = pytest.mark.db


def error_of(response: Any) -> tuple[str, str]:
    """(path, error code) of a failure redirect to the public landing page."""
    assert response.status_code == 303, response.text
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}" == WEB_ORIGIN
    return target.path, parse_qs(target.query)["error"][0]


# --- Starting


def test_start_redirects_to_google_and_binds_the_browser(
    browser: TestClient, factory: sessionmaker[Session]
) -> None:
    response = browser.get(START, follow_redirects=False)
    assert response.status_code == 302
    assert urlsplit(response.headers["location"]).netloc == "accounts.google.com"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    with factory() as session:
        row = session.scalars(select(OAuthState)).one()
    assert len(row.state_hash) == 32
    assert row.return_path == "/en"


@pytest.mark.parametrize("value", ["fr", "EN", ""])
def test_start_rejects_unknown_locales(browser: TestClient, value: str) -> None:
    assert browser.get(START, params={"locale": value}, follow_redirects=False).status_code == 422


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example/x",
        "/\\evil.example",
        "evil.example",
        "/en/../../x",
        "/en?next=https://evil.example",
        "/en#x",
        "javascript:alert(1)",
        "/" + "a" * 250,
    ],
)
def test_start_refuses_anything_but_a_local_path(browser: TestClient, target: str) -> None:
    response = browser.get(START, params={"return_to": target}, follow_redirects=False)
    assert response.status_code == 422


def test_sign_in_returns_to_an_allowed_local_path(browser: TestClient, google: FakeGoogle) -> None:
    flow = begin(browser, locale="tr", return_to="/tr/dashboard")
    response = finish(browser, google, flow)
    assert response.headers["location"] == f"{WEB_ORIGIN}/tr/dashboard"


def test_start_cleans_up_expired_and_used_flows(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    finish(browser, google, begin(browser))
    begin(browser)
    assert count(factory, OAuthState) == 1


def test_google_outage_at_start_stores_nothing(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    google.down = True
    response = browser.get(START, params={"locale": "ar"}, follow_redirects=False)
    assert error_of(response) == ("/ar", "google_unavailable")
    assert count(factory, OAuthState) == 0


def test_without_configuration_google_sign_in_is_unavailable(
    test_database_url: str, factory: sessionmaker[Session]
) -> None:
    app = create_app(make_settings(database_url=test_database_url))
    with TestClient(app) as client:
        for path in (START, CALLBACK):
            response = client.get(path, follow_redirects=False)
            # A page load: it ends on the landing page, and the setting's name isn't exposed.
            assert error_of(response) == ("/en", "google_unavailable")


def test_a_failure_with_no_attempt_to_read_uses_the_browsers_language(browser: TestClient) -> None:
    browser.cookies.set("fp_locale", "tr")
    response = browser.get(
        CALLBACK, params={"state": "unknown", "code": "x"}, follow_redirects=False
    )
    assert error_of(response) == ("/tr", "failed")


def test_a_made_up_language_cookie_is_ignored(browser: TestClient) -> None:
    browser.cookies.set("fp_locale", "evil.example")
    response = browser.get(CALLBACK, params={"state": "unknown"}, follow_redirects=False)
    assert error_of(response) == ("/en", "failed")


def test_an_internal_failure_is_reported_as_unreachable_and_hides_the_details(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_: Any, **__: Any) -> None:
        raise RuntimeError("database password in a stack trace")

    monkeypatch.setattr("app.api.v1.auth.complete_sign_in", explode)
    browser.cookies.set("fp_locale", "ar")
    response = browser.get(CALLBACK, params={"state": "s", "code": "c"}, follow_redirects=False)
    assert error_of(response) == ("/ar", "unreachable")
    assert "password" not in response.headers["location"] and not response.content


def test_too_many_attempts_end_on_the_landing_page(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise ApiError(429, "rate_limited", "Too many requests")

    monkeypatch.setattr("app.api.v1.auth.start_sign_in", refuse)
    response = browser.get(START, params={"locale": "tr"}, follow_redirects=False)
    assert error_of(response) == ("/tr", "failed")


def test_a_malformed_start_request_stays_a_plain_error(browser: TestClient) -> None:
    response = browser.get(
        START, params={"return_to": "https://evil.example"}, follow_redirects=False
    )
    assert response.status_code == 422
    assert "location" not in response.headers


def test_no_outcome_ever_points_at_the_removed_sign_in_page(
    browser: TestClient, google: FakeGoogle
) -> None:
    google.down = True
    down = browser.get(START, follow_redirects=False)
    google.down = False
    denied = browser.get(
        CALLBACK,
        params={"state": begin(browser).state, "error": "access_denied"},
        follow_redirects=False,
    )
    bad_state = browser.get(CALLBACK, params={"state": "nope", "code": "x"}, follow_redirects=False)
    for response in (down, denied, bad_state):
        assert "sign-in" not in response.headers["location"]


# --- Completing


def test_first_sign_in_creates_user_identity_and_session(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    response = finish(browser, google, begin(browser, locale="ar"))

    assert response.status_code == 303
    assert response.headers["location"] == f"{WEB_ORIGIN}/ar"
    assert response.headers["cache-control"] == "no-store"
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(f"{SESSION_COOKIE}="))
    csrf_cookie = next(c for c in cookies if c.startswith(f"{CSRF_COOKIE}="))
    assert "httponly" in session_cookie.lower() and "samesite=lax" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()  # The web app must read this one.
    assert BINDING_COOKIE not in browser.cookies

    with factory() as session:
        user = session.scalars(select(User)).one()
        identity = session.scalars(select(UserIdentity)).one()
        stored = session.scalars(select(UserSession)).one()
    assert identity.user_id == user.id == stored.user_id
    assert (user.email, user.locale, user.display_name) == (
        "alex@example.com",
        "ar",
        "Alex Example",
    )
    # Only hashes are stored.
    token = browser.cookies[SESSION_COOKIE]
    assert stored.token_hash == sessions.hash_token(token)
    assert token.encode() not in stored.token_hash
    assert stored.csrf_secret == sessions.hash_token(browser.cookies[CSRF_COOKIE])
    # One transaction, so all three share a timestamp; their order isn't meaningful.
    assert sorted(actions(factory)) == [
        "auth.google.account_created",
        "auth.google.signed_in",
        "auth.session.created",
    ]


def test_returning_user_is_recognised_by_subject_not_email(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    finish(browser, google, begin(browser))
    finish(browser, google, begin(browser), email="alex.new@example.com", name="Ignored")

    assert count(factory, User) == 1
    with factory() as session:
        user = session.scalars(select(User)).one()
    assert user.email == "alex.new@example.com"  # The copy follows Google.
    assert user.display_name == "Alex Example"  # The profile is the user's, not Google's.
    assert "auth.google.email_changed" in actions(factory)


def test_signing_in_again_rotates_the_session(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    finish(browser, google, begin(browser))
    first = browser.cookies[SESSION_COOKIE]
    finish(browser, google, begin(browser))
    second = browser.cookies[SESSION_COOKIE]
    assert first != second
    with factory() as session:
        rows = session.scalars(select(UserSession).order_by(UserSession.created_at)).all()
    assert [r.revoked_at is not None for r in rows] == [True, False]


def test_a_session_cookie_planted_before_sign_in_is_never_adopted(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """Session fixation: the server always issues a fresh token, whatever the browser had."""
    browser.cookies.set(SESSION_COOKIE, "attacker-chosen-value")
    response = finish(browser, google, begin(browser))
    issued = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith(f"{SESSION_COOKIE}=")
    )
    assert "attacker-chosen-value" not in issued
    with factory() as session:
        stored = session.scalars(select(UserSession.token_hash)).one()
    assert stored != sessions.hash_token("attacker-chosen-value")


def test_unverified_email_creates_no_account_and_no_session(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    response = finish(browser, google, begin(browser, locale="tr"), email_verified=False)
    assert error_of(response) == ("/tr", "email_not_verified")
    assert count(factory, User) == 0
    assert count(factory, UserSession) == 0
    assert SESSION_COOKIE not in browser.cookies


def test_a_reassigned_email_gets_a_separate_account(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """A new Google account using an address someone else had is a different person."""
    finish(browser, google, begin(browser), subject="first-owner")
    finish(browser, google, begin(browser), subject="second-owner")
    with factory() as session:
        pairs = session.execute(
            select(UserIdentity.provider_subject, User.id, User.email).join(User)
        ).all()
    assert len(pairs) == 2
    assert pairs[0].id != pairs[1].id
    assert pairs[0].email == pairs[1].email == "alex@example.com"


@pytest.mark.parametrize("status", ["suspended", "deleted"])
def test_suspended_and_deleted_accounts_get_no_session(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session], status: str
) -> None:
    finish(browser, google, begin(browser))
    browser.cookies.clear()
    with factory.begin() as session:
        session.execute(text("UPDATE users SET status = :s"), {"s": status})
    response = finish(browser, google, begin(browser))
    assert error_of(response) == ("/en", "account_unavailable")
    assert SESSION_COOKIE not in browser.cookies
    assert count(factory, UserSession) == 1  # Only the one from before.
    assert actions(factory)[-1] == "auth.google.rejected"


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else.apps.googleusercontent.com"},
        {"iss": "https://evil.example"},
        {"exp": 1, "iat": 0},
        {"sub": ""},
        {"email": "not-an-email", "email_verified": True},
    ],
)
def test_invalid_id_tokens_fail_generically_and_create_nothing(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session], claims: dict[str, Any]
) -> None:
    response = finish(browser, google, begin(browser), **claims)
    _, code = error_of(response)
    assert code in ("failed", "email_not_verified")
    assert count(factory, User) == 0
    assert count(factory, UserSession) == 0


def test_a_token_with_another_flows_nonce_is_rejected(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    flow = begin(browser)
    code = google.issue_code(challenge=flow.challenge, id_token=make_id_token(nonce="x" * 43))
    response = browser.get(
        CALLBACK, params={"state": flow.state, "code": code}, follow_redirects=False
    )
    assert error_of(response)[1] == "failed"
    assert count(factory, User) == 0


# --- State, replay, and login CSRF


def test_a_callback_can_be_used_only_once(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    flow = begin(browser)
    code = google.issue_code(challenge=flow.challenge, id_token=make_id_token(nonce=flow.nonce))
    params = {"state": flow.state, "code": code}
    assert browser.get(CALLBACK, params=params, follow_redirects=False).status_code == 303
    browser.cookies.set(BINDING_COOKIE, flow.binding)
    replay = browser.get(CALLBACK, params=params, follow_redirects=False)
    assert error_of(replay)[1] == "failed"
    assert count(factory, User) == 1
    assert count(factory, UserSession) == 1  # The replay created no second session.


@pytest.mark.parametrize("state", [None, "", "made-up-state"])
def test_missing_or_unknown_state_is_rejected(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session], state: str | None
) -> None:
    flow = begin(browser)
    code = google.issue_code(challenge=flow.challenge, id_token=make_id_token(nonce=flow.nonce))
    params = {"code": code} | ({"state": state} if state is not None else {})
    assert error_of(browser.get(CALLBACK, params=params, follow_redirects=False))[1] == "failed"
    assert google.grants  # The code was never even exchanged.
    assert count(factory, UserSession) == 0


def test_expired_state_is_rejected(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    flow = begin(browser)
    with factory.begin() as session:
        session.execute(
            text(
                "UPDATE oauth_states SET created_at = now() - interval '2 hours', "
                "expires_at = now() - interval '1 hour'"
            )
        )
    assert error_of(finish(browser, google, flow))[1] == "failed"
    assert count(factory, User) == 0


def test_another_browser_cannot_finish_the_flow(
    auth_app: FastAPI, browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    """Login CSRF: an attacker's state and code, delivered to a victim's browser, must fail."""
    attacker_flow = begin(browser)
    code = google.issue_code(
        challenge=attacker_flow.challenge, id_token=make_id_token(nonce=attacker_flow.nonce)
    )
    params = {"state": attacker_flow.state, "code": code}
    with TestClient(auth_app, raise_server_exceptions=False) as victim:
        victim.cookies.set(BINDING_COOKIE, "the-victims-own-unrelated-cookie")
        response = victim.get(CALLBACK, params=params, follow_redirects=False)
        assert SESSION_COOKIE not in victim.cookies
    assert error_of(response)[1] == "failed"
    assert count(factory, User) == 0
    browser.cookies.set(BINDING_COOKIE, attacker_flow.binding)
    assert error_of(browser.get(CALLBACK, params=params, follow_redirects=False))[1] == "failed"


def test_a_callback_without_the_binding_cookie_is_refused(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    flow = begin(browser)
    browser.cookies.clear()
    assert error_of(finish(browser, google, flow))[1] == "failed"
    assert count(factory, User) == 0


@pytest.mark.parametrize(
    ("error", "code"), [("access_denied", "cancelled"), ("server_error", "failed")]
)
def test_google_errors_burn_the_state_and_show_no_provider_text(
    browser: TestClient, google: FakeGoogle, error: str, code: str
) -> None:
    flow = begin(browser, locale="ar")
    response = browser.get(
        CALLBACK,
        params={"state": flow.state, "error": error, "error_description": "<script>x</script>"},
        follow_redirects=False,
    )
    assert error_of(response) == ("/ar", code)
    assert "script" not in response.headers["location"]
    browser.cookies.set(BINDING_COOKIE, flow.binding)
    assert error_of(finish(browser, google, flow))[1] == "failed"


def test_a_forged_code_is_rejected_by_google(
    browser: TestClient, factory: sessionmaker[Session]
) -> None:
    flow = begin(browser)
    response = browser.get(
        CALLBACK, params={"state": flow.state, "code": "forged"}, follow_redirects=False
    )
    assert error_of(response)[1] == "failed"
    assert actions(factory) == ["auth.google.rejected"]


def test_google_outage_during_callback_is_reported_and_the_state_stays_used(
    browser: TestClient, google: FakeGoogle
) -> None:
    flow = begin(browser)
    code = google.issue_code(challenge=flow.challenge, id_token=make_id_token(nonce=flow.nonce))
    google.token_status = 503
    params = {"state": flow.state, "code": code}
    assert error_of(browser.get(CALLBACK, params=params, follow_redirects=False))[1] == (
        "google_unavailable"
    )
    google.token_status = None
    browser.cookies.set(BINDING_COOKIE, flow.binding)
    assert error_of(browser.get(CALLBACK, params=params, follow_redirects=False))[1] == "failed"


def test_concurrent_callbacks_with_one_state_have_exactly_one_winner(
    factory: sessionmaker[Session], google: FakeGoogle, browser: TestClient
) -> None:
    flow = begin(browser)
    client = google.client()
    outcomes: list[str] = []
    barrier = threading.Barrier(4)

    def attempt() -> None:
        barrier.wait()
        with factory() as session:
            try:
                code = google.issue_code(
                    challenge=flow.challenge, id_token=make_id_token(nonce=flow.nonce)
                )
                google_signin.complete_sign_in(
                    session, client, state=flow.state, binding=flow.binding, code=code
                )
                session.commit()
                outcomes.append("won")
            except google_signin.SignInRejectedError as exc:
                outcomes.append(exc.reason)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(outcomes) == ["invalid_state"] * 3 + ["won"]
    assert count(factory, User) == 1


# --- Account creation rules, through the service

CLAIMS = GoogleClaims(
    subject="race-subject", email="race@example.com", email_verified=True, name="Race"
)


def test_concurrent_first_sign_ins_create_one_user_and_one_identity(
    factory: sessionmaker[Session],
) -> None:
    results: list[object] = []
    barrier = threading.Barrier(4)

    def sign_in() -> None:
        barrier.wait()
        with factory() as session:
            try:
                user, created = google_signin.resolve_user(session, CLAIMS, locale="en")
                session.commit()
                results.append((user.id, created))
            except Exception as exc:
                results.append(exc)

    threads = [threading.Thread(target=sign_in) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(isinstance(r, tuple) for r in results), results
    assert len({r[0] for r in results}) == 1  # type: ignore[index]
    assert sum(bool(r[1]) for r in results) == 1  # type: ignore[index]
    assert count(factory, User) == 1
    assert count(factory, UserIdentity) == 1


def test_a_failure_while_issuing_the_session_leaves_nothing_behind(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database failure after the user is flushed: no user, identity, session, or cookie."""

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("database write failed")

    monkeypatch.setattr("app.api.v1.auth.issue_session", broken)
    response = finish(browser, google, begin(browser))
    assert error_of(response) == ("/en", "unreachable")
    assert SESSION_COOKIE not in browser.cookies
    assert count(factory, User) == 0
    assert count(factory, UserIdentity) == 0
    assert count(factory, UserSession) == 0


def test_a_failing_audit_write_rolls_back_the_whole_sign_in(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_audit(*_: object, **__: object) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(google_signin, "audit", broken_audit)
    assert error_of(finish(browser, google, begin(browser))) == ("/en", "unreachable")
    assert count(factory, User) == 0
    assert count(factory, UserSession) == 0


def test_no_database_transaction_is_open_while_google_is_called(
    factory: sessionmaker[Session], google: FakeGoogle, browser: TestClient
) -> None:
    flow = begin(browser)
    real = google.client()
    seen: list[bool] = []

    class Watching:
        def __init__(self, session: Session) -> None:
            self.session = session

        def exchange_code(self, *, code: str, code_verifier: str) -> str:
            seen.append(self.session.in_transaction())
            return real.exchange_code(code=code, code_verifier=code_verifier)

        def verify_id_token(self, id_token: str, *, nonce: str) -> GoogleClaims:
            return real.verify_id_token(id_token, nonce=nonce)

        def authorization_url(self, **kwargs: str) -> str:
            raise AssertionError

    code = google.issue_code(challenge=flow.challenge, id_token=make_id_token(nonce=flow.nonce))
    with factory() as session:
        google_signin.complete_sign_in(
            session, Watching(session), state=flow.state, binding=flow.binding, code=code
        )
        session.rollback()
    assert seen == [False]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alex‮evil", "Alexevil"),
        ("  Many   spaces  ", "Many spaces"),
        ("​​", google_signin.FALLBACK_DISPLAY_NAME),
        (None, google_signin.FALLBACK_DISPLAY_NAME),
        ("x" * 300, "x" * 100),
    ],
)
def test_display_names_from_google_are_sanitised(raw: str | None, expected: str) -> None:
    assert google_signin.safe_display_name(raw) == expected


# --- Sensitive data


def test_no_secret_token_code_or_identity_value_reaches_logs_responses_or_audit(
    browser: TestClient,
    google: FakeGoogle,
    factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    flow = begin(browser)
    id_token = make_id_token(nonce=flow.nonce, email="private.person@example.com")
    code = google.issue_code(challenge=flow.challenge, id_token=id_token)
    params = {"state": flow.state, "code": code}
    responses = [browser.get(CALLBACK, params=params, follow_redirects=False)]
    session_token = browser.cookies[SESSION_COOKIE]
    csrf_token = browser.cookies[CSRF_COOKIE]
    browser.cookies.set(BINDING_COOKIE, flow.binding)
    responses.append(browser.get(CALLBACK, params=params, follow_redirects=False))
    responses.append(browser.get("/api/v1/auth/me"))

    with factory() as session:
        audit_text = str(
            [
                (e.action, e.event_metadata, e.entity_type, e.request_id)
                for e in session.scalars(select(AuditEvent))
            ]
        )
    # Bodies and redirect targets. Set-Cookie on the first response carries the token by design.
    exposed = caplog.text + audit_text
    exposed += " ".join(r.text + r.headers.get("location", "") for r in responses)
    for secret in (
        CLIENT_SECRET,
        id_token,
        code,
        flow.state,
        flow.nonce,
        flow.binding,
        session_token,
        csrf_token,
        "google-subject-1",
    ):
        assert secret not in exposed, secret
    assert "private.person" not in caplog.text + audit_text
