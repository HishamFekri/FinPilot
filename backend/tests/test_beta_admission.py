"""Who may create an account: open, allowlist or closed. Existing accounts always sign in."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Environment
from app.db.models import AuditEvent, User, UserSession
from tests.auth_harness import SESSION_COOKIE, begin, count, finish, sign_in, write_headers
from tests.conftest import make_settings
from tests.google_fakes import CLIENT_ID, CLIENT_SECRET

PRODUCTION = {
    "app_env": Environment.PRODUCTION,
    "google_client_id": CLIENT_ID,
    "google_client_secret": CLIENT_SECRET,
    "google_redirect_uri": "https://finpilot.example.com/api/v1/auth/google/callback",
    "web_origin": "https://finpilot.example.com",
    "trusted_proxies": "172.30.10.2",
}


def _policy(browser: TestClient, **settings: Any) -> None:
    app: Any = browser.app
    app.state.settings = app.state.settings.model_copy(update=settings)


def _error(response: Any) -> str:
    assert response.status_code == 303
    return str(response.headers["location"]).split("error=", 1)[1].split("&", 1)[0]


# --- Settings: what production accepts


def test_production_defaults_to_public_registration() -> None:
    settings = make_settings(**PRODUCTION)
    assert settings.effective_registration_mode == "open"
    assert settings.admits_new_account("anyone@example.com")


def test_an_allowlist_in_production_needs_addresses() -> None:
    with pytest.raises(ValueError, match="needs BETA_ALLOWLIST"):
        make_settings(**PRODUCTION, registration_mode="allowlist")
    settings = make_settings(
        **PRODUCTION,
        registration_mode="allowlist",
        beta_allowlist="Owner@Example.com\nfriend@example.org",
    )
    assert settings.effective_registration_mode == "allowlist"
    assert settings.beta_allowlist_addresses == {"owner@example.com", "friend@example.org"}
    assert settings.admits_new_account("owner@example.com")
    assert settings.admits_new_account("  OWNER@example.COM ")
    assert not settings.admits_new_account("stranger@example.com")


def test_production_accepts_open_registration_with_or_without_a_list() -> None:
    assert make_settings(**PRODUCTION, registration_mode="open").admits_new_account("a@b.example")
    listed = make_settings(**PRODUCTION, registration_mode="open", beta_allowlist="o@example.com")
    assert listed.admits_new_account("stranger@example.com")


def test_closed_admits_no_new_account_even_if_listed() -> None:
    settings = make_settings(
        **PRODUCTION, registration_mode="closed", beta_allowlist="o@example.com"
    )
    assert not settings.admits_new_account("o@example.com")


def test_development_and_tests_stay_open_unless_told_otherwise() -> None:
    assert make_settings().effective_registration_mode == "open"
    assert make_settings().admits_new_account("anyone@example.com")


@pytest.mark.parametrize("value", ["not-an-address", "@example.com", "owner@"])
def test_an_allowlist_entry_must_be_an_address(value: str) -> None:
    with pytest.raises(ValueError, match="BETA_ALLOWLIST must list email addresses"):
        make_settings(beta_allowlist=value)


# --- Sign-in, end to end through the fake Google


def test_an_unlisted_verified_address_creates_nothing(
    browser: TestClient, google: Any, factory: sessionmaker[Session]
) -> None:
    _policy(browser, registration_mode="allowlist", beta_allowlist="owner@example.com")
    response = finish(browser, google, begin(browser, locale="ar"), email="alex@example.com")
    assert _error(response) == "registration_closed"
    assert "/ar" in response.headers["location"]
    assert count(factory, User) == 0
    assert count(factory, UserSession) == 0
    assert SESSION_COOKIE not in browser.cookies
    with factory() as session:
        reasons: list[Any] = list(session.scalars(select(AuditEvent.event_metadata)).all())
    assert any((r or {}).get("reason") == "registration_closed" for r in reasons)
    assert "alex@example.com" not in str(reasons)


def test_the_owner_on_the_list_gets_in_and_existing_accounts_keep_signing_in(
    browser: TestClient, google: Any, factory: sessionmaker[Session]
) -> None:
    _policy(browser, registration_mode="allowlist", beta_allowlist="Owner@Example.com")
    response = finish(browser, google, begin(browser), email="owner@example.com")
    assert response.status_code == 303 and "error=" not in response.headers["location"]
    assert count(factory, User) == 1
    # Closing registration later never locks out someone who already has an account.
    _policy(browser, registration_mode="closed")
    browser.cookies.clear()
    again = finish(browser, google, begin(browser), email="owner@example.com")
    assert "error=" not in again.headers["location"]
    newcomer = finish(
        browser, google, begin(browser), subject="someone-else", email="new@example.com"
    )
    assert _error(newcomer) == "registration_closed"
    assert count(factory, User) == 1


def test_public_registration_gives_a_newcomer_a_clean_account_and_nothing_of_anyone_elses(
    browser: TestClient, google: Any, factory: sessionmaker[Session]
) -> None:
    _policy(browser, registration_mode="open")
    api = "/api/v1"
    first = write_headers(sign_in(browser, google, subject="first-user", email="first@example.com"))
    assert browser.post(f"{api}/setup", json={"currency": "EUR"}, headers=first).status_code < 300
    account = browser.post(
        f"{api}/accounts",
        json={"name": "First's bank", "type": "bank", "currency": "EUR",
              "opening_balance": {"amount": "500.00", "currency": "EUR"}},
        headers={**first, "Idempotency-Key": str(uuid.uuid4())},
    )  # fmt: skip
    assert account.status_code == 201, account.text
    trip = browser.post(
        f"{api}/trips",
        json={"name": "First's trip", "base_currency": "EUR"},
        headers={**first, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert trip.status_code == 201, trip.text

    # Anyone with a verified Google address now gets in, with an account of their own.
    browser.cookies.clear()
    sign_in(browser, google, subject="newcomer", email="newcomer@example.com")
    assert count(factory, User) == 2
    assert browser.get(f"{api}/auth/me").json()["email"] == "newcomer@example.com"
    # Clean: nothing set up, no accounts, no trips, no transactions...
    assert browser.get(f"{api}/accounts").json() == []
    assert browser.get(f"{api}/trips").json() == []
    assert browser.get(f"{api}/transactions").json()["items"] == []
    # ...and the first person's records stay theirs, even by ID.
    assert browser.get(f"{api}/accounts/{account.json()['id']}").status_code == 404
    assert browser.get(f"{api}/trips/{trip.json()['id']}").status_code == 404
