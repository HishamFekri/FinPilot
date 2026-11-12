"""The Google profile picture: kept from the verified ID token, refreshed at every sign-in, and
returned by /auth/me as a plain image URL. Anything that isn't https on Google's image host is
dropped, and the database refuses it too."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import UserIdentity
from app.services.google_oidc import safe_picture_url
from tests.auth_harness import begin, finish
from tests.google_fakes import FakeGoogle

ME = "/api/v1/auth/me"
PICTURE = "https://lh3.googleusercontent.com/a/ACg8ocJ-example=s96-c"
NEW_PICTURE = "https://lh4.googleusercontent.com/a/ACg8ocK-other=s96-c"


@pytest.mark.parametrize("value", [PICTURE, NEW_PICTURE, "https://lh3.googleusercontent.com/x"])
def test_pictures_on_googles_image_host_are_kept(value: str) -> None:
    assert safe_picture_url(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        42,
        ["https://lh3.googleusercontent.com/a/x"],
        "http://lh3.googleusercontent.com/a/x",  # not https
        "https://lh3.googleusercontent.com",  # no path
        "https://lh3.googleusercontent.com:8443/a/x",  # a port
        "https://user@lh3.googleusercontent.com/a/x",  # credentials
        "https://evil.example/lh3.googleusercontent.com/a/x",
        "https://googleusercontent.com.evil.example/a/x",
        "https://a.b.googleusercontent.com/a/x",  # a nested host
        "https://LH3.googleusercontent.com/a/x",  # not in canonical form
        "https://lh3.googleusercontent.com/a/x y",  # whitespace
        "https://lh3.googleusercontent.com/a/‮x",  # a bidi control
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "https://lh3.googleusercontent.com/" + "a" * 2048,  # too long
    ],
)
def test_anything_else_is_dropped(value: object) -> None:
    assert safe_picture_url(value) is None


@pytest.mark.db
def test_first_sign_in_keeps_the_picture_and_me_returns_it(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    finish(browser, google, begin(browser), picture=PICTURE)
    assert browser.get(ME).json()["avatar_url"] == PICTURE
    with factory() as session:
        assert session.scalars(select(UserIdentity.provider_picture_url)).one() == PICTURE


@pytest.mark.db
def test_the_picture_follows_google_at_every_sign_in(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session]
) -> None:
    finish(browser, google, begin(browser), picture=PICTURE)
    finish(browser, google, begin(browser), picture=NEW_PICTURE)
    assert browser.get(ME).json()["avatar_url"] == NEW_PICTURE
    # Removed at Google: removed here, and the app shows initials.
    finish(browser, google, begin(browser))
    assert browser.get(ME).json()["avatar_url"] is None


@pytest.mark.db
def test_an_unusable_picture_claim_signs_in_without_a_picture(
    browser: TestClient, google: FakeGoogle
) -> None:
    response = finish(browser, google, begin(browser), picture="https://evil.example/me.png")
    assert response.status_code == 303
    body = browser.get(ME).json()
    assert body["avatar_url"] is None
    assert body["display_name"] == "Alex Example"


@pytest.mark.db
def test_me_carries_no_token_or_subject(browser: TestClient, google: FakeGoogle) -> None:
    finish(browser, google, begin(browser), picture=PICTURE)
    body = browser.get(ME).json()
    assert set(body) == {
        "id", "email", "display_name", "avatar_url", "locale", "preferred_currency",
        "time_zone", "status", "created_at",
    }  # fmt: skip


@pytest.mark.db
@pytest.mark.parametrize(
    "value", ["https://evil.example/a.png", "http://lh3.googleusercontent.com/a/x"]
)
def test_the_database_refuses_other_urls(
    browser: TestClient, google: FakeGoogle, factory: sessionmaker[Session], value: str
) -> None:
    finish(browser, google, begin(browser))
    with factory() as session, pytest.raises(IntegrityError):
        session.execute(update(UserIdentity).values(provider_picture_url=value))
        session.flush()
