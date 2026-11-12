"""The production stack, tested from outside: through the TLS proxy, like a browser.

These run against a stack that's already running, never against anything they start themselves:
- deploy/scripts/stack-test.sh runs them in a container on the compose stack;
- tests_stack/local_stack.py runs them on this machine without Docker.

Settings, all from the environment (`NAME_FILE` works for each URL):
  STACK_URL                  where to connect, such as https://finpilot.test:8443
  STACK_ORIGIN               the site's public origin, sent as Origin on changes
  STACK_CA                   the proxy's CA certificate. TLS is always verified
  STACK_OWNER_DATABASE_URL   the schema owner: only to create test people and sessions
  STACK_RUNTIME_DATABASE_URL the app's restricted role: to check RLS and its limits directly
  STACK_HTTP_URL             optional: the plain-HTTP address, to check the redirect
  STACK_PHASE                "main" (default), "after_restart", "load" or "behind"
  STACK_DIRECT               "1": no proxy in front (local_stack.py without Caddy). Tests that
                             only the proxy can pass are skipped, and say so
  STACK_PROXY_URL            behind phase: the FinPilot proxy itself, skipping the edge
  STACK_HOST                 behind phase: the site's host name, sent to STACK_PROXY_URL

Phases: "main" runs through the FinPilot proxy in direct mode; "behind" runs the same tests
through a stand-in for the server's existing proxy, with the FinPilot proxy in behind mode.

Test people are synthetic, with addresses under example.test. Nothing here deletes data.
"""

import ssl
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import read_secret
from app.db.models import User
from app.services.sessions import issue_session

API = "/api/v1"
PERSISTENT_EMAIL = "stack-persistence@example.test"


def setting(name: str, *, required: bool = True) -> str:
    value = read_secret(name)
    if not value and required:
        pytest.exit(f"{name} isn't set; see tests_stack/conftest.py", returncode=2)
    return value or ""


def phase() -> str:
    return setting("STACK_PHASE", required=False) or "main"


def direct() -> bool:
    return setting("STACK_DIRECT", required=False) == "1"


needs_proxy = pytest.mark.skipif(
    direct(), reason="needs the TLS proxy in front, which this run doesn't have"
)


@pytest.fixture(scope="session")
def origin() -> str:
    return setting("STACK_ORIGIN").rstrip("/")


@pytest.fixture(scope="session")
def tls() -> ssl.SSLContext | None:
    if direct():
        return None
    return ssl.create_default_context(cafile=setting("STACK_CA"))


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(setting("STACK_OWNER_DATABASE_URL"), poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def runtime_engine() -> Iterator[Engine]:
    engine = create_engine(setting("STACK_RUNTIME_DATABASE_URL"), poolclass=NullPool)
    yield engine
    engine.dispose()


def client(tls: ssl.SSLContext | None, **options: Any) -> httpx.Client:
    return httpx.Client(
        base_url=setting("STACK_URL"),
        verify=tls if tls is not None else True,
        timeout=60,
        follow_redirects=False,
        **options,
    )


@pytest.fixture
def anonymous(tls: ssl.SSLContext | None) -> Iterator[httpx.Client]:
    with client(tls) as browser:
        yield browser


@dataclass
class Person:
    id: uuid.UUID
    email: str
    browser: httpx.Client
    csrf: str
    origin: str

    def headers(self, **extra: str) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf, "Origin": self.origin, **extra}

    def keyed(self) -> dict[str, str]:
        return self.headers(**{"Idempotency-Key": str(uuid.uuid4())})


def signed_in(
    owner_engine: Engine, tls: ssl.SSLContext | None, origin: str, email: str | None = None
) -> Person:
    """A synthetic person with a real session, issued the way sign-in issues one.

    Google can't be used here, so the session is created directly, as the schema owner. Everything
    after that goes through the proxy and the app's normal checks.
    """
    email = email or f"stack-{uuid.uuid4().hex[:12]}@example.test"
    with Session(owner_engine) as db:
        user = db.query(User).filter_by(email=email).one_or_none()
        if user is None:
            user = User(email=email, display_name="Stack Test", locale="en")
            db.add(user)
            db.flush()
        issued = issue_session(
            db,
            user,
            idle=timedelta(hours=1),
            absolute=timedelta(hours=2),
            user_agent="finpilot-stack-test",
        )
        db.commit()
        user_id = user.id
    browser = client(tls)
    # Production cookie names: Secure, host-only (__Host-).
    browser.cookies.set("__Host-fp_session", issued.token)
    browser.cookies.set("__Host-fp_csrf", issued.csrf_token)
    return Person(user_id, email, browser, issued.csrf_token, origin)


@pytest.fixture
def person(owner_engine: Engine, tls: ssl.SSLContext | None, origin: str) -> Iterator[Person]:
    someone = signed_in(owner_engine, tls, origin)
    yield someone
    someone.browser.close()


def ok(response: httpx.Response) -> Any:
    assert response.status_code < 300, f"{response.status_code} {response.text[:500]}"
    return response.json() if response.content else None


# Tests without a marker hold in both proxy modes.
DEFAULT_PHASES = ("main", "behind")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Each phase runs only its own tests: the restart check needs the stack restarted first."""
    wanted = phase()
    for item in items:
        marker = item.get_closest_marker("phase")
        phases = tuple(marker.args) if marker else DEFAULT_PHASES
        if wanted not in phases:
            item.add_marker(pytest.mark.skip(reason=f"runs in the {', '.join(phases)} phase"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "phase(*names): which stack-test phases run this test")
