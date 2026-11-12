"""A stand-in for Google's HTTP endpoints, used only by tests.

It signs ID tokens with a key generated here, serves that key from a fake JWKS endpoint, and checks
PKCE and single-use codes the way Google does. The production client and PyJWT verification run
unchanged against it. No real Google account, token, or credential is ever involved.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.google_oidc import DISCOVERY_URL, GoogleOidcClient

CLIENT_ID = "finpilot-test.apps.googleusercontent.com"
CLIENT_SECRET = "test-only-client-secret-not-real"
REDIRECT_URI = "http://localhost:8000/api/v1/auth/google/callback"
KEY_ID = "test-key-1"
DISCOVERY = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}


@lru_cache
def signing_key(name: str = "google") -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key: rsa.RSAPrivateKey, key_id: str = KEY_ID) -> dict[str, Any]:
    jwk: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**jwk, "kid": key_id, "alg": "RS256", "use": "sig"}


def make_id_token(
    *,
    nonce: str | None,
    subject: str = "google-subject-1",
    email: str | None = "alex@example.com",
    email_verified: object = True,
    name: str | None = "Alex Example",
    key: rsa.RSAPrivateKey | None = None,
    key_id: str = KEY_ID,
    algorithm: str = "RS256",
    **overrides: Any,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": subject,
        "iat": now,
        "exp": now + 3600,
        "nonce": nonce,
        "email_verified": email_verified,
    }
    if email is not None:
        claims["email"] = email
    if name is not None:
        claims["name"] = name
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    signer: Any = key or signing_key()
    return jwt.encode(claims, signer, algorithm=algorithm, headers={"kid": key_id})


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass
class _Grant:
    challenge: str
    id_token: str


@dataclass
class FakeGoogle:
    """Mimics Google's discovery, JWKS, and token endpoints. Records requests for assertions."""

    discovery: dict[str, Any] = field(default_factory=lambda: dict(DISCOVERY))
    keys: list[dict[str, Any]] = field(default_factory=lambda: [public_jwk(signing_key())])
    down: bool = False
    token_status: int | None = None
    grants: dict[str, _Grant] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def issue_code(self, *, challenge: str, id_token: str) -> str:
        code = "code-" + secrets.token_urlsafe(16)
        self.grants[code] = _Grant(challenge=challenge, id_token=id_token)
        return code

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("unreachable", request=request)
        url = str(request.url)
        if url == DISCOVERY_URL:
            return httpx.Response(200, json=self.discovery)
        if url == DISCOVERY["jwks_uri"]:
            return httpx.Response(200, json={"keys": self.keys})
        if url == DISCOVERY["token_endpoint"] and request.method == "POST":
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        if self.token_status is not None:
            return httpx.Response(self.token_status, json={"error": "server_error"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        grant = self.grants.pop(form.get("code", ""), None)  # Single use, like Google.
        valid = (
            grant is not None
            and form.get("client_id") == CLIENT_ID
            and form.get("client_secret") == CLIENT_SECRET
            and form.get("redirect_uri") == REDIRECT_URI
            and form.get("grant_type") == "authorization_code"
            and pkce_challenge(form.get("code_verifier", "")) == grant.challenge
        )
        if not valid or grant is None:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "x"})
        return httpx.Response(200, json={"id_token": grant.id_token, "token_type": "Bearer"})

    def client(self) -> GoogleOidcClient:
        return GoogleOidcClient(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            timeout_seconds=2,
            transport=httpx.MockTransport(self.handler),
        )


def authorization_params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
