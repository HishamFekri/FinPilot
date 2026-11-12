"""Real-browser regression tests: the real API against finpilot_test, the real web app in Chrome.

How it fits together:
- The API runs in this process on 127.0.0.1 (port E2E_API_PORT, default 8101) with the test
  suite's fake Google and fake FX providers, against TEST_DATABASE_URL only. That database is
  reset and migrated first; the same two guards as the unit tests refuse anything not named
  *_test, before and after connecting.
- The web app is the local dev server (E2E_WEB, default http://localhost:3000); start it with
  `npm run dev` in apps/web. Pages load from it, and the browser's /api/v1 requests are
  forwarded to the test API, so no special build or configuration is needed.
- Test people sign in through the fake Google. Their session cookies live only in memory here;
  nothing is written to disk.

Run from backend/:  uv run pytest tests_e2e
These tests are not part of the default `pytest` run (testpaths = tests).
"""

import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import pytest
import uvicorn
from alembic import command
from dotenv import dotenv_values
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

import tests.auth_harness as harness
from app.api.v1.auth import get_google_provider
from app.core.config import BACKEND_DIR
from app.core.rate_limit import RateLimiter
from app.db import session as session_module
from app.main import create_app
from tests.auth_harness import CSRF_COOKIE, SESSION_COOKIE, sign_in, write_headers
from tests.conftest import (
    alembic_config,
    assert_connected_to_test_database,
    assert_safe_test_database,
    harden_if_runtime,
    make_settings,
    reset_test_schema,
    runtime_database_url,
)
from tests.google_fakes import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, FakeGoogle
from tests.test_fx import FakeProviders

API = "/api/v1"
WEB = os.environ.get("E2E_WEB", "http://localhost:3000").rstrip("/")
API_PORT = int(os.environ.get("E2E_API_PORT", "8101"))
LOGIN = f"{API}/__e2e/login"
GOOGLE_AUTH = "https://accounts.google.com/"


def _reachable(url: str) -> bool:
    try:
        with urlopen(url, timeout=5):  # noqa: S310 - a local URL the developer chose
            return True
    except Exception:
        return False


class Person:
    """A test person signed in through the fake Google, used to seed data over the real API."""

    def __init__(self, app: FastAPI, google: FakeGoogle, name: str) -> None:
        self.name = name
        self.client = TestClient(app, base_url=f"http://127.0.0.1:{API_PORT}")
        self.client.__enter__()
        self.csrf = sign_in(
            self.client,
            google,
            subject=f"e2e-{name.lower()}",
            email=f"{name.lower()}@example.test",
            name=name,
        )
        self.id: str = self.client.get(f"{API}/auth/me").json()["id"]

    def post(self, path: str, body: Any = None) -> Any:
        headers = {**write_headers(self.csrf, WEB), "Idempotency-Key": str(uuid.uuid4())}
        response = self.client.post(f"{API}{path}", json=body, headers=headers)
        assert response.status_code < 300, (path, response.status_code)
        return response.json() if response.content else None

    def patch(self, path: str, body: Any) -> Any:
        response = self.client.patch(
            f"{API}{path}", json=body, headers=write_headers(self.csrf, WEB)
        )
        assert response.status_code < 300, (path, response.status_code)
        return response.json()

    def get(self, path: str) -> Any:
        response = self.client.get(f"{API}{path}")
        assert response.status_code == 200, (path, response.status_code)
        return response.json()

    def cookies(self) -> tuple[str, str]:
        return self.client.cookies[SESSION_COOKIE], self.client.cookies[CSRF_COOKIE]

    def category(self, key: str) -> str:
        return str(next(c for c in self.get("/categories") if c["system_key"] == key)["id"])


@dataclass
class World:
    """What the seed created, for tests to refer to by name."""

    people: dict[str, Person]
    accounts: dict[str, str] = field(default_factory=dict)
    trip: str = ""
    plan: str = ""


def _seed(people: dict[str, Person]) -> World:
    world = World(people=people)
    today = datetime.now(UTC).date()
    for person in people.values():
        person.patch("/auth/me", {"time_zone": "Europe/Istanbul"})
        person.post("/setup", {"currency": "EUR"})
        account = person.post("/accounts", {
            "name": f"{person.name}'s bank", "type": "bank", "currency": "EUR",
            "opening_balance": {"amount": "1000.00", "currency": "EUR"},
        })  # fmt: skip
        world.accounts[person.name] = account["id"]

    alice, bob = people["Alice"], people["Bob"]
    # One purchase per dialog test, so each test starts from an untouched entry.
    for i in range(1, 9):
        alice.post("/transactions", {
            "type": "expense", "account_id": world.accounts["Alice"],
            "amount": {"amount": f"{40 + i}.00", "currency": "EUR"},
            "category_id": alice.category("groceries"),
            "effective_date": (today - timedelta(days=i % 3)).isoformat(),
            "description": f"E2E purchase {i}",
        })  # fmt: skip
    alice.post("/transactions", {
        "type": "income", "account_id": world.accounts["Alice"],
        "amount": {"amount": "2400.00", "currency": "EUR"},
        "category_id": alice.category("salary"), "description": "Salary",
    })  # fmt: skip
    # Bob's private entry: Alice must never see it.
    bob.post("/transactions", {
        "type": "expense", "account_id": world.accounts["Bob"],
        "amount": {"amount": "77.00", "currency": "EUR"},
        "category_id": bob.category("shopping"), "description": "Bob private purchase",
    })  # fmt: skip
    # A shared trip, so trip pages have content.
    trip = alice.post("/trips", {
        "name": "E2E trip", "base_currency": "EUR", "destination": "Porto",
        "start_date": today.isoformat(), "end_date": today.isoformat(),
    })["id"]  # fmt: skip
    token = alice.post(f"/trips/{trip}/invitations", {"label": "Bob"})["token"]
    bob.post("/invitations/accept", {"token": token})
    alice.post(f"/trips/{trip}/expenses", {
        "payer_user_id": alice.id, "description": "E2E dinner", "category_key": "food_and_drinks",
        "expense_date": today.isoformat(), "amount": {"amount": "60.00", "currency": "EUR"},
        "split": {"method": "equal", "shares": [{"user_id": alice.id}, {"user_id": bob.id}]},
    })  # fmt: skip
    alice.post("/goals", {"name": "E2E goal", "target": {"amount": "500.00", "currency": "EUR"},
                          "starting": "100.00"})  # fmt: skip
    # A second currency, for Quick Add's account choice and the totals in another currency.
    usd = alice.post("/accounts", {
        "name": "Alice's dollars", "type": "bank", "currency": "USD",
        "opening_balance": {"amount": "300.00", "currency": "USD"},
    })  # fmt: skip
    world.accounts["Alice USD"] = usd["id"]
    # An installment plan: a car bought for 1200 with 200 down, 10 monthly installments.
    plan = alice.post("/installments", {
        "origin": "purchase", "name": "E2E car", "lender": "E2E bank", "currency": "EUR",
        "installment_count": 10, "first_due_date": (today + timedelta(days=20)).isoformat(),
        "item_price": "1200.00", "down_payment": "200.00",
        "category_id": alice.category("transportation"), "account_id": world.accounts["Alice"],
    })  # fmt: skip
    world.plan = plan["id"]
    world.trip = trip
    return world


@dataclass
class Server:
    world: World
    cookies: dict[str, tuple[str, str]]
    google: FakeGoogle
    app: FastAPI


@pytest.fixture(scope="session")
def server() -> Iterator[Server]:
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get(
        "TEST_DATABASE_URL"
    )
    if not url:
        pytest.skip("TEST_DATABASE_URL isn't set")
    if not _reachable(WEB):
        pytest.skip(f"the web app isn't running at {WEB} (npm run dev in apps/web)")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", API_PORT)) == 0:
            pytest.skip(f"port {API_PORT} is in use; set E2E_API_PORT")

    assert_safe_test_database(url)
    # Bounded: Home loads its sections in parallel, and the test role allows few connections.
    engine = create_engine(url, poolclass=QueuePool, pool_size=6, max_overflow=0, pool_timeout=30)
    assert_connected_to_test_database(engine)
    reset_test_schema(engine)
    command.upgrade(alembic_config(url), "head")
    harden_if_runtime(engine)
    runtime = runtime_database_url()
    app_engine = engine
    if runtime:
        app_engine = create_engine(
            runtime, poolclass=QueuePool, pool_size=6, max_overflow=0, pool_timeout=30
        )
    factory = sessionmaker(app_engine, expire_on_commit=False)
    session_module._session_factory = lambda: factory  # type: ignore[assignment]
    harness.WEB_ORIGIN = WEB  # sign_in checks the redirect goes back to this web origin

    google = FakeGoogle()
    app = create_app(
        make_settings(
            database_url=url,
            google_client_id=CLIENT_ID,
            google_client_secret=CLIENT_SECRET,
            google_redirect_uri=REDIRECT_URI,
            web_origin=WEB,
        )
    )
    fake_client = google.client()
    app.dependency_overrides[get_google_provider] = lambda: fake_client
    app.state.fx = FakeProviders().gateway()

    # Dan and Eve sign out in test_shell.py, which revokes their sessions: nobody else uses them.
    # Fayen, Fayar and Faytr have their sessions ended in test_sign_in_flow.py.
    names = ("Alice", "Bob", "Carol", "Dan", "Eve", "Fayen", "Fayar", "Faytr")
    people = {name: Person(app, google, name) for name in names}
    world = _seed(people)
    cookies = {name: person.cookies() for name, person in people.items()}

    @app.get(f"{LOGIN}/{{name}}")
    def _login(name: str) -> Response:
        """Exists only in this test process: signs the browser in as a seeded test person."""
        session, csrf = cookies[name]
        out = Response(status_code=204)
        out.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="lax", path="/")
        out.set_cookie(CSRF_COOKIE, csrf, samesite="lax", path="/")
        return out

    logging.getLogger("finpilot.access").setLevel(logging.WARNING)
    config = uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="warning")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not uv.started:
        assert time.monotonic() < deadline, "the test API didn't start"
        time.sleep(0.05)
    try:
        yield Server(world=world, cookies=cookies, google=google, app=app)
    finally:
        uv.should_exit = True
        thread.join(timeout=10)
        for person in people.values():
            person.client.__exit__(None, None, None)
        engine.dispose()


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(playwright: Playwright) -> Iterator[Browser]:
    channel = os.environ.get("E2E_BROWSER_CHANNEL", "chrome")
    headless = os.environ.get("E2E_HEADED") != "1"
    instance = playwright.chromium.launch(channel=channel or None, headless=headless)
    yield instance
    instance.close()


@dataclass
class Traffic:
    """Every API request the page made, for asserting exactly which writes happened."""

    requests: list[tuple[str, str, int]] = field(default_factory=list)
    # The error code of each failed write (a stable code, never personal data).
    errors: list[str] = field(default_factory=list)
    # Stands in for Google's consent screen: from the authorization request's query, the query
    # Google would send back to the callback. Redirects aren't re-routed by Playwright, so the
    # hop to accounts.google.com is resolved here and the real callback is called directly.
    google: Callable[[dict[str, str]], str] | None = None

    def writes(self, contains: str = "") -> list[tuple[str, str, int]]:
        return [
            r
            for r in self.requests
            if r[0] not in ("GET", "HEAD", "OPTIONS") and contains in r[1] and LOGIN not in r[1]
        ]

    def failures(self) -> list[tuple[str, str, int]]:
        return [r for r in self.requests if r[2] >= 500]


@dataclass
class Session:
    context: BrowserContext
    page: Page
    traffic: Traffic
    console_errors: list[str]
    world: World

    def login(self, name: str) -> None:
        self.page.goto(f"{WEB}/en")
        status = self.page.evaluate(
            "async (u) => (await fetch(u, {credentials: 'include'})).status", f"{LOGIN}/{name}"
        )
        assert status == 204

    def go(self, path: str, locale: str = "en") -> None:
        self.page.goto(f"{WEB}/{locale}{path}")
        self.page.wait_for_load_state("networkidle")


def _forward(traffic: Traffic) -> Any:
    def handle(route: Route) -> None:
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1]
        target = f"http://127.0.0.1:{API_PORT}/{path}"
        try:
            response = route.fetch(url=target, max_redirects=0)
        except PlaywrightError:
            # The local server dropped an idle keep-alive connection: retry once, as a browser does.
            response = route.fetch(url=target, max_redirects=0)
        traffic.requests.append((request.method, "/" + path, response.status))
        location = response.headers.get("location", "")
        if location.startswith(GOOGLE_AUTH):
            assert traffic.google, "a test clicked Google without saying how Google answers"
            answer = traffic.google(
                {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
            )
            # The browser would have stored this cookie on the way to Google.
            binding = response.headers["set-cookie"].split(";", 1)[0]
            headers = request.all_headers()
            headers["cookie"] = "; ".join(filter(None, [headers.get("cookie"), binding]))
            response = route.fetch(
                url=f"http://127.0.0.1:{API_PORT}{API}/auth/google/callback?{answer}",
                headers=headers,
                max_redirects=0,
            )
            traffic.requests.append(("GET", f"{API}/auth/google/callback", response.status))
        if response.status >= 400 and request.method != "GET":
            traffic.errors.append(f"{response.status} {response.text()[:160]}")
        route.fulfill(response=response)

    return handle


@pytest.fixture
def session(server: Server, browser: Browser) -> Iterator[Session]:
    # Each test is its own visitor: the per-minute limits must not carry over from the last one.
    server.app.state.rate_limiter = RateLimiter()
    context = browser.new_context(viewport={"width": 1366, "height": 768}, locale="en-US")
    traffic = Traffic()
    context.route(f"{WEB}{API}/**", _forward(traffic))
    # The dev server's own indicator floats over the phone tab bar; real users never see it.
    context.add_init_script(
        "addEventListener('DOMContentLoaded', () => document.head.insertAdjacentHTML("
        "'beforeend', '<style>nextjs-portal{display:none!important}</style>'))"
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield Session(context, page, traffic, errors, server.world)
    # A request still in flight when the test ends is dropped, not forwarded to a closed context.
    context.unroute_all(behavior="ignoreErrors")
    context.close()
