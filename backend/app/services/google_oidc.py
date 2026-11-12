"""The only code that talks to Google. Everything above it sees verified claims or an exception.

Endpoints come from Google's discovery document, as Google recommends. ID tokens are verified with
PyJWT: signature against Google's published keys, then issuer, audience, expiry, and nonce. No
cryptography is implemented here.

Nothing in this module logs or raises with a token, code, secret, or claim value.
"""

import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx2 as httpx
import jwt

from app.core.config import Settings

DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
# Sign-in only. No Gmail, Drive, Contacts, or any API access is requested.
SCOPES = "openid email profile"
ALLOWED_ENDPOINT_HOSTS = {"accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"}
CACHE_SECONDS = 3600
KEY_REFRESH_MIN_SECONDS = 60
CLOCK_LEEWAY_SECONDS = 10
_SUBJECT = re.compile(r"^[\x21-\x7e]{1,255}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")
# Google's profile pictures are served from its image host. Anything else is ignored, so a picture
# can only ever be a plain https URL there: no other host, port, credentials, or odd characters.
PICTURE_HOST_SUFFIX = ".googleusercontent.com"
PICTURE_MAX_LENGTH = 2048
_PICTURE_CHARS = re.compile(r"^[\x21-\x7e]+$")


class GoogleUnavailableError(Exception):
    """Google couldn't be reached, or answered with something unusable. Safe to retry later."""


class GoogleRejectedError(Exception):
    """The provider or its token failed a check. `reason` is a fixed code, never token content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GoogleClaims:
    """Verified claims. Personal values are left out of repr, so tracebacks don't carry them."""

    subject: str = field(repr=False)
    email: str | None = field(repr=False)
    email_verified: bool
    name: str | None = field(repr=False)
    # The `picture` claim (scope `profile`), when it's a URL on Google's image host. Display only.
    picture: str | None = field(default=None, repr=False)


class GoogleIdentityProvider(Protocol):
    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str: ...

    def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Returns the ID token from Google's token endpoint."""
        ...

    def verify_id_token(self, id_token: str, *, nonce: str) -> GoogleClaims: ...


class GoogleOidcClient:
    """Production implementation. `transport` exists only so tests can stand in for Google."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._timeout = timeout_seconds
        self._transport = transport
        self._lock = threading.Lock()
        self._discovery: dict[str, str] | None = None
        self._discovery_at = 0.0
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_at = 0.0

    def __repr__(self) -> str:
        return "GoogleOidcClient()"

    # --- HTTP

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self._timeout, transport=self._transport, follow_redirects=False
        )

    def _get_json(self, url: str) -> Mapping[str, Any]:
        try:
            with self._client() as client:
                response = client.get(url)
        except httpx.HTTPError:
            raise GoogleUnavailableError("request failed") from None
        if response.status_code != 200:
            raise GoogleUnavailableError("unexpected status")
        try:
            data = response.json()
        except ValueError:
            raise GoogleUnavailableError("invalid JSON") from None
        if not isinstance(data, dict):
            raise GoogleUnavailableError("invalid JSON")
        return data

    def _endpoints(self) -> dict[str, str]:
        with self._lock:
            if self._discovery and time.monotonic() - self._discovery_at < CACHE_SECONDS:
                return self._discovery
        data = self._get_json(DISCOVERY_URL)
        endpoints: dict[str, str] = {}
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = data.get(key)
            parts = urlsplit(value) if isinstance(value, str) else None
            # A tampered or broken document must not send users or secrets anywhere else.
            if (
                parts is None
                or parts.scheme != "https"
                or parts.hostname not in (ALLOWED_ENDPOINT_HOSTS)
            ):
                raise GoogleUnavailableError("unexpected discovery document")
            endpoints[key] = value  # type: ignore[assignment]
        if data.get("issuer") not in ISSUERS:
            raise GoogleUnavailableError("unexpected discovery document")
        with self._lock:
            self._discovery, self._discovery_at = endpoints, time.monotonic()
        return endpoints

    # --- Flow

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                # No refresh token: FinPilot never calls Google on anyone's behalf.
                "access_type": "online",
                "prompt": "select_account",
            }
        )
        return f"{self._endpoints()['authorization_endpoint']}?{query}"

    def exchange_code(self, *, code: str, code_verifier: str) -> str:
        token_endpoint = self._endpoints()["token_endpoint"]
        try:
            with self._client() as client:
                response = client.post(
                    token_endpoint,
                    data={
                        "code": code,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "redirect_uri": self._redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": code_verifier,
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError:
            raise GoogleUnavailableError("token request failed") from None
        if response.status_code in (400, 401):
            # Usually a reused, expired, or forged code. Google's message isn't passed on.
            raise GoogleRejectedError("code_rejected")
        if response.status_code != 200:
            raise GoogleUnavailableError("unexpected status")
        try:
            id_token = response.json().get("id_token")
        except (ValueError, AttributeError):
            raise GoogleUnavailableError("invalid token response") from None
        if not isinstance(id_token, str) or not id_token:
            raise GoogleRejectedError("no_id_token")
        return id_token

    # --- Verification

    def _signing_key(self, key_id: str) -> jwt.PyJWK:
        with self._lock:
            age = time.monotonic() - self._keys_at
            if age < CACHE_SECONDS and key_id in self._keys:
                return self._keys[key_id]
        # Unknown keys trigger a refetch, since Google rotates them, but not more than once a
        # minute, so forged key IDs can't make us hammer Google. Stale keys are always refetched.
        if age >= KEY_REFRESH_MIN_SECONDS:
            data = self._get_json(self._endpoints()["jwks_uri"])
            try:
                key_set = jwt.PyJWKSet.from_dict(dict(data))
            except jwt.PyJWKSetError:
                raise GoogleUnavailableError("invalid key set") from None
            keys = {key.key_id: key for key in key_set.keys if key.key_id}
            with self._lock:
                self._keys, self._keys_at = keys, time.monotonic()
        with self._lock:
            key = self._keys.get(key_id)
        if key is None:
            raise GoogleRejectedError("unknown_key")
        return key

    def verify_id_token(self, id_token: str, *, nonce: str) -> GoogleClaims:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError:
            raise GoogleRejectedError("malformed_token") from None
        key_id = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(key_id, str):
            raise GoogleRejectedError("unexpected_algorithm")
        key = self._signing_key(key_id)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=ISSUERS,
                leeway=CLOCK_LEEWAY_SECONDS,
                options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise GoogleRejectedError("expired") from None
        except jwt.InvalidAudienceError:
            raise GoogleRejectedError("wrong_audience") from None
        except jwt.InvalidIssuerError:
            raise GoogleRejectedError("wrong_issuer") from None
        except jwt.InvalidSignatureError:
            raise GoogleRejectedError("bad_signature") from None
        except jwt.InvalidTokenError:
            raise GoogleRejectedError("invalid_claims") from None
        return _checked_claims(claims, client_id=self._client_id, nonce=nonce)


def _checked_claims(claims: Mapping[str, Any], *, client_id: str, nonce: str) -> GoogleClaims:
    # A token meant for several audiences, or issued to another party, isn't for this web client.
    if claims.get("aud") != client_id or claims.get("azp", client_id) != client_id:
        raise GoogleRejectedError("wrong_audience")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not secrets.compare_digest(
        token_nonce.encode(), nonce.encode()
    ):
        raise GoogleRejectedError("wrong_nonce")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not _SUBJECT.fullmatch(subject):
        raise GoogleRejectedError("invalid_subject")
    email = claims.get("email")
    if not isinstance(email, str) or len(email) > 254 or not _EMAIL.fullmatch(email):
        email = None
    verified = claims.get("email_verified")
    name = claims.get("name")
    return GoogleClaims(
        subject=subject,
        email=email,
        # Only a real boolean true counts. Strings and anything else mean unverified.
        email_verified=verified is True and email is not None,
        name=name if isinstance(name, str) else None,
        picture=safe_picture_url(claims.get("picture")),
    )


def safe_picture_url(value: object) -> str | None:
    """The picture URL if it's an https address on Google's image host, else None."""
    if not isinstance(value, str) or not 0 < len(value) <= PICTURE_MAX_LENGTH:
        return None
    if not _PICTURE_CHARS.fullmatch(value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.netloc != host
        or not host.endswith(PICTURE_HOST_SUFFIX)
        or not re.fullmatch(r"[a-z0-9-]+", host.removesuffix(PICTURE_HOST_SUFFIX))
        # A path is required, as the database check requires one.
        or not value.startswith(f"https://{host}/")
    ):
        return None
    return value


def build_google_provider(settings: Settings) -> GoogleIdentityProvider | None:
    """The real client, or None when Google isn't configured. There's no other implementation."""
    if not settings.google_configured:
        return None
    assert settings.google_client_id and settings.google_client_secret  # noqa: S101 - validated
    assert settings.google_redirect_uri  # noqa: S101 - validated together in Settings
    return GoogleOidcClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        redirect_uri=settings.google_redirect_uri,
        timeout_seconds=settings.google_http_timeout_seconds,
    )
