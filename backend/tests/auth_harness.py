"""Shared helpers for tests that sign in through the real routes against a fake Google.

Fixtures live in tests/conftest.py. Google is replaced only at the HTTP layer, and only here.
"""

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuditEvent
from tests.google_fakes import FakeGoogle, authorization_params, make_id_token

START = "/api/v1/auth/google/start"
CALLBACK = "/api/v1/auth/google/callback"
WEB_ORIGIN = "http://localhost:3000"
BINDING_COOKIE = "fp_oauth"
SESSION_COOKIE = "fp_session"
CSRF_COOKIE = "fp_csrf"


@dataclass
class Flow:
    state: str
    nonce: str
    challenge: str
    binding: str


def begin(browser: TestClient, **params: str) -> Flow:
    response = browser.get(START, params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    query = authorization_params(response.headers["location"])
    return Flow(
        state=query["state"],
        nonce=query["nonce"],
        challenge=query["code_challenge"],
        binding=browser.cookies[BINDING_COOKIE],
    )


def finish(browser: TestClient, google: FakeGoogle, flow: Flow, **claims: Any) -> Any:
    code = google.issue_code(
        challenge=flow.challenge, id_token=make_id_token(nonce=flow.nonce, **claims)
    )
    return browser.get(CALLBACK, params={"state": flow.state, "code": code}, follow_redirects=False)


def sign_in(browser: TestClient, google: FakeGoogle, **claims: Any) -> str:
    """Signs in through Google and returns the CSRF token the web app would read."""
    response = finish(browser, google, begin(browser), **claims)
    assert response.status_code == 303, response.text
    assert response.headers["location"].startswith(WEB_ORIGIN)
    return browser.cookies[CSRF_COOKIE]


def write_headers(csrf: str | None, origin: str | None = WEB_ORIGIN) -> dict[str, str]:
    headers = {}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    if origin is not None:
        headers["Origin"] = origin
    return headers


def count(factory: sessionmaker[Session], model: type[Any]) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def actions(factory: sessionmaker[Session]) -> list[str]:
    with factory() as session:
        return list(
            session.scalars(
                select(AuditEvent.action).order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
        )
