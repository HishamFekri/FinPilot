"""ID token verification and the Google client, with no database and no network."""

import time
from typing import Any

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Environment
from app.services.google_oidc import (
    SCOPES,
    GoogleOidcClient,
    GoogleRejectedError,
    GoogleUnavailableError,
    build_google_provider,
)
from tests.conftest import make_settings
from tests.google_fakes import (
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    FakeGoogle,
    authorization_params,
    make_id_token,
    public_jwk,
    signing_key,
)

NONCE = "n" * 43


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def client(google: FakeGoogle) -> GoogleOidcClient:
    return google.client()


def _rejected(client: GoogleOidcClient, token: str, reason: str) -> None:
    with pytest.raises(GoogleRejectedError) as raised:
        client.verify_id_token(token, nonce=NONCE)
    assert raised.value.reason == reason


# --- Valid tokens


def test_a_valid_token_yields_its_verified_claims(client: GoogleOidcClient) -> None:
    claims = client.verify_id_token(make_id_token(nonce=NONCE), nonce=NONCE)
    assert claims.subject == "google-subject-1"
    assert claims.email == "alex@example.com"
    assert claims.email_verified is True
    assert claims.name == "Alex Example"


def test_the_legacy_issuer_form_is_accepted(client: GoogleOidcClient) -> None:
    token = make_id_token(nonce=NONCE, iss="accounts.google.com")
    assert client.verify_id_token(token, nonce=NONCE).subject == "google-subject-1"


def test_claims_repr_carries_no_personal_values(client: GoogleOidcClient) -> None:
    claims = client.verify_id_token(make_id_token(nonce=NONCE), nonce=NONCE)
    assert "alex" not in repr(claims).lower()
    assert "google-subject-1" not in repr(claims)


# --- Each check, individually


def test_wrong_issuer_is_rejected(client: GoogleOidcClient) -> None:
    _rejected(client, make_id_token(nonce=NONCE, iss="https://evil.example"), "wrong_issuer")


@pytest.mark.parametrize(
    "audience",
    ["someone-else.apps.googleusercontent.com", [CLIENT_ID, "other.apps.googleusercontent.com"]],
)
def test_wrong_or_shared_audience_is_rejected(client: GoogleOidcClient, audience: Any) -> None:
    _rejected(client, make_id_token(nonce=NONCE, aud=audience), "wrong_audience")


def test_a_token_issued_to_another_party_is_rejected(client: GoogleOidcClient) -> None:
    token = make_id_token(nonce=NONCE, azp="android.apps.googleusercontent.com")
    _rejected(client, token, "wrong_audience")


def test_expired_token_is_rejected(client: GoogleOidcClient) -> None:
    past = int(time.time()) - 3600
    _rejected(client, make_id_token(nonce=NONCE, iat=past - 60, exp=past), "expired")


def test_token_issued_in_the_future_is_rejected(client: GoogleOidcClient) -> None:
    future = int(time.time()) + 3600
    _rejected(client, make_id_token(nonce=NONCE, iat=future, exp=future + 60), "invalid_claims")


def test_token_signed_by_another_key_is_rejected(client: GoogleOidcClient) -> None:
    token = make_id_token(nonce=NONCE, key=signing_key("attacker"))
    _rejected(client, token, "bad_signature")


def test_tampered_payload_is_rejected(client: GoogleOidcClient) -> None:
    header, _, signature = make_id_token(nonce=NONCE).split(".")
    forged = make_id_token(nonce=NONCE, subject="victim").split(".")[1]
    _rejected(client, f"{header}.{forged}.{signature}", "bad_signature")


def test_unknown_key_id_is_rejected(client: GoogleOidcClient) -> None:
    _rejected(client, make_id_token(nonce=NONCE, key_id="not-a-google-key"), "unknown_key")


def test_symmetric_algorithm_confusion_is_rejected(client: GoogleOidcClient) -> None:
    """Only RS256 is accepted, so an HMAC token can't be verified with a public key as secret."""
    token = jwt.encode(
        {"sub": "x", "aud": CLIENT_ID, "nonce": NONCE},
        "an-hmac-secret-of-at-least-32-bytes!",
        algorithm="HS256",
        headers={"kid": "test-key-1"},
    )
    _rejected(client, token, "unexpected_algorithm")


def test_unsigned_token_is_rejected(client: GoogleOidcClient) -> None:
    token = jwt.encode({"sub": "x", "aud": CLIENT_ID}, None, algorithm="none")  # type: ignore[arg-type]
    _rejected(client, token, "unexpected_algorithm")


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "eyJhbGciOiJSUzI1NiJ9.e30."])
def test_malformed_tokens_are_rejected(client: GoogleOidcClient, token: str) -> None:
    with pytest.raises(GoogleRejectedError):
        client.verify_id_token(token, nonce=NONCE)


@pytest.mark.parametrize("nonce", ["wrong-nonce-value-wrong-nonce-value-wrong", None])
def test_wrong_or_missing_nonce_is_rejected(client: GoogleOidcClient, nonce: str | None) -> None:
    _rejected(client, make_id_token(nonce=nonce), "wrong_nonce")


@pytest.mark.parametrize("claim", ["sub", "exp", "iat", "aud", "iss"])
def test_missing_required_claims_are_rejected(client: GoogleOidcClient, claim: str) -> None:
    removed: dict[str, Any] = {claim: None}
    with pytest.raises(GoogleRejectedError):
        client.verify_id_token(make_id_token(nonce=NONCE, **removed), nonce=NONCE)


@pytest.mark.parametrize("subject", ["", " leading-space", "x" * 256, "ü-non-ascii"])
def test_malformed_subjects_are_rejected(client: GoogleOidcClient, subject: str) -> None:
    _rejected(client, make_id_token(nonce=NONCE, subject=subject), "invalid_subject")


def test_non_string_subject_is_rejected(client: GoogleOidcClient) -> None:
    with pytest.raises(GoogleRejectedError):
        client.verify_id_token(make_id_token(nonce=NONCE, subject=12345), nonce=NONCE)  # type: ignore[arg-type]


# --- Email claims


@pytest.mark.parametrize("value", [False, "true", "True", 1, None])
def test_only_a_boolean_true_counts_as_verified(client: GoogleOidcClient, value: object) -> None:
    token = make_id_token(nonce=NONCE, email_verified=value)
    assert client.verify_id_token(token, nonce=NONCE).email_verified is False


@pytest.mark.parametrize("email", ["no-at-sign", "two@@x.com", "has space@x.com", 42])
def test_malformed_email_is_dropped_and_never_counts_as_verified(
    client: GoogleOidcClient, email: object
) -> None:
    claims = client.verify_id_token(make_id_token(nonce=NONCE, email=email), nonce=NONCE)  # type: ignore[arg-type]
    assert claims.email is None
    assert claims.email_verified is False


def test_missing_email_is_none(client: GoogleOidcClient) -> None:
    claims = client.verify_id_token(make_id_token(nonce=NONCE, email=None), nonce=NONCE)
    assert (claims.email, claims.email_verified) == (None, False)


# --- Keys and discovery


def test_rotated_keys_are_fetched_on_an_unknown_key_id(google: FakeGoogle) -> None:
    client = google.client()
    client.verify_id_token(make_id_token(nonce=NONCE), nonce=NONCE)
    new_key = signing_key("rotated")
    google.keys = [public_jwk(new_key, "test-key-2")]
    # The first lookup was under a minute ago, so the refetch is throttled.
    _rejected(client, make_id_token(nonce=NONCE, key=new_key, key_id="test-key-2"), "unknown_key")
    client._keys_at -= 120
    token = make_id_token(nonce=NONCE, key=new_key, key_id="test-key-2")
    assert client.verify_id_token(token, nonce=NONCE).subject == "google-subject-1"


def test_discovery_and_keys_are_cached(google: FakeGoogle, client: GoogleOidcClient) -> None:
    for _ in range(3):
        client.verify_id_token(make_id_token(nonce=NONCE), nonce=NONCE)
    assert len(google.requests) == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("token_endpoint", "https://evil.example/token"),
        ("authorization_endpoint", "http://accounts.google.com/o/oauth2/v2/auth"),
        ("issuer", "https://evil.example"),
        ("jwks_uri", None),
    ],
)
def test_a_tampered_discovery_document_is_refused(
    google: FakeGoogle, client: GoogleOidcClient, key: str, value: str | None
) -> None:
    google.discovery[key] = value
    with pytest.raises(GoogleUnavailableError):
        client.authorization_url(state="s", nonce="n", code_challenge="c")


def test_google_outage_is_unavailable_not_rejected(
    google: FakeGoogle, client: GoogleOidcClient
) -> None:
    google.down = True
    with pytest.raises(GoogleUnavailableError):
        client.verify_id_token(make_id_token(nonce=NONCE), nonce=NONCE)


# --- Authorization URL and code exchange


def test_authorization_url_requests_sign_in_only_with_pkce(client: GoogleOidcClient) -> None:
    url = client.authorization_url(state="the-state", nonce="the-nonce", code_challenge="chal")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    params = authorization_params(url)
    assert params == {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": "the-state",
        "nonce": "the-nonce",
        "code_challenge": "chal",
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    assert SCOPES.split() == ["openid", "email", "profile"]
    assert CLIENT_SECRET not in url


def test_code_exchange_rejection_hides_googles_message(
    google: FakeGoogle, client: GoogleOidcClient
) -> None:
    with pytest.raises(GoogleRejectedError) as raised:
        client.exchange_code(code="never-issued", code_verifier="v" * 43)
    assert raised.value.reason == "code_rejected"
    assert "invalid_grant" not in str(raised.value)


def test_code_exchange_server_error_is_unavailable(
    google: FakeGoogle, client: GoogleOidcClient
) -> None:
    google.token_status = 503
    with pytest.raises(GoogleUnavailableError):
        client.exchange_code(code="c", code_verifier="v" * 43)


def test_client_repr_never_shows_the_secret(client: GoogleOidcClient) -> None:
    assert CLIENT_SECRET not in repr(client)


# --- Configuration


GOOGLE = {
    "google_client_id": CLIENT_ID,
    "google_client_secret": CLIENT_SECRET,
    "google_redirect_uri": REDIRECT_URI,
}


def test_google_is_off_without_configuration() -> None:
    assert build_google_provider(make_settings()) is None


def test_configured_google_builds_the_real_client_and_hides_the_secret() -> None:
    settings = make_settings(**GOOGLE)
    assert isinstance(build_google_provider(settings), GoogleOidcClient)
    assert CLIENT_SECRET not in repr(settings)
    assert CLIENT_SECRET not in str(settings.model_dump())


def test_blank_values_count_as_unset() -> None:
    settings = make_settings(google_client_id=" ", google_client_secret="", google_redirect_uri="")
    assert not settings.google_configured


@pytest.mark.parametrize("missing", list(GOOGLE))
def test_partial_google_configuration_is_refused(missing: str) -> None:
    with pytest.raises(ValidationError, match="go together"):
        make_settings(**{k: v for k, v in GOOGLE.items() if k != missing})


@pytest.mark.parametrize(
    ("uri", "env"),
    [
        ("http://finpilot.example/api/v1/auth/google/callback", Environment.TEST),
        ("http://localhost:8000/api/v1/auth/google/callback", Environment.PRODUCTION),
        ("https://finpilot.example/api/v1/auth/google/elsewhere", Environment.TEST),
        ("https://finpilot.example/api/v1/auth/google/callback?next=x", Environment.TEST),
        ("https://user:pw@finpilot.example/api/v1/auth/google/callback", Environment.TEST),
        ("javascript:alert(1)", Environment.TEST),
    ],
)
def test_unsafe_redirect_uris_are_refused(uri: str, env: Environment) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{**GOOGLE, "google_redirect_uri": uri, "app_env": env})


def test_production_requires_google_and_https() -> None:
    with pytest.raises(ValidationError, match="Production needs Google"):
        make_settings(app_env=Environment.PRODUCTION)
    settings = make_settings(
        **{
            **GOOGLE,
            "app_env": Environment.PRODUCTION,
            "google_redirect_uri": "https://finpilot.example/api/v1/auth/google/callback",
            "web_origin": "https://finpilot.example",
            "trusted_proxies": "172.30.10.2",
        }
    )
    assert settings.secure_cookies


def test_the_demo_never_uses_google() -> None:
    with pytest.raises(ValidationError, match="demo never uses Google"):
        make_settings(**GOOGLE, app_env=Environment.DEMO)


def test_client_id_must_look_like_a_google_client_id() -> None:
    with pytest.raises(ValidationError, match="Google OAuth client ID"):
        make_settings(**{**GOOGLE, "google_client_id": "my-app"})


def test_validation_errors_never_echo_the_client_secret() -> None:
    with pytest.raises(ValidationError) as raised:
        make_settings(**{**GOOGLE, "google_client_id": "bad"})
    assert CLIENT_SECRET not in str(raised.value)
