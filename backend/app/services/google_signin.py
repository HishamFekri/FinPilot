"""Google sign-in: starting the flow, and turning a callback into a FinPilot user.

Transactions:
- consuming the state is committed before Google is called, so a failure afterwards can't make
  that state usable again, and no database connection is held during the network call;
- the user, identity, session, and their audit events are then written in ONE transaction, which
  the caller commits. If any part fails, none of it exists, and no cookie is set;
- rejections are committed with their audit event before the error is raised.
"""

import base64
import hashlib
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import OAuthState, User, UserIdentity
from app.services.google_oidc import (
    GoogleClaims,
    GoogleIdentityProvider,
    GoogleRejectedError,
    GoogleUnavailableError,
)
from app.services.sessions import SIGN_IN_STATUSES, audit

logger = logging.getLogger(__name__)

PROVIDER = "google"
FALLBACK_DISPLAY_NAME = "FinPilot user"
# A path on our own site only. No scheme, host, backslash, query, or protocol-relative "//".
_RETURN_PATH = re.compile(r"^/[A-Za-z0-9._~/-]{0,199}$")


class SignInRejectedError(Exception):
    """The attempt failed. `reason` is an internal code; responses stay generic."""

    def __init__(self, reason: str, locale: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.locale = locale


@dataclass(frozen=True)
class StartedSignIn:
    authorization_url: str
    browser_binding: str  # Goes into an HttpOnly cookie, and nowhere else.

    def __repr__(self) -> str:
        return "StartedSignIn()"


@dataclass(frozen=True)
class SignedIn:
    user: User
    created: bool
    locale: str
    return_path: str


def safe_return_path(path: str | None) -> str | None:
    """The path if it's safe to redirect to, otherwise None."""
    if path is None:
        return None
    if not _RETURN_PATH.fullmatch(path) or path.startswith("//") or "/../" in f"{path}/":
        return None
    return path


def _sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256: base64url of SHA-256, without padding. Uses only the standard library."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _reject(db: Session, reason: str, locale: str, user: User | None = None) -> SignInRejectedError:
    """Records a rejection that got past state checks, commits it, and returns the error."""
    db.rollback()
    audit(db, "auth.google.rejected", user.id if user else None, reason=reason)
    db.commit()
    logger.warning("Google sign-in rejected", extra={"reason": reason})
    return SignInRejectedError(reason, locale)


def start_sign_in(
    db: Session,
    provider: GoogleIdentityProvider,
    *,
    locale: str,
    return_path: str,
    ttl_seconds: int,
) -> StartedSignIn:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    binding = secrets.token_urlsafe(32)
    # Built first: if Google's discovery document can't be fetched, nothing is stored.
    url = provider.authorization_url(
        state=state, nonce=nonce, code_challenge=_pkce_challenge(verifier)
    )
    db.execute(
        delete(OAuthState).where(
            or_(OAuthState.expires_at < func.now(), OAuthState.consumed_at.is_not(None))
        )
    )
    db.add(
        OAuthState(
            state_hash=_sha256(state),
            browser_binding_hash=_sha256(binding),
            nonce=nonce,
            pkce_verifier=verifier,
            locale=locale,
            return_path=return_path,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
    )
    return StartedSignIn(authorization_url=url, browser_binding=binding)


@dataclass(frozen=True)
class _Flow:
    nonce: str
    pkce_verifier: str
    locale: str
    return_path: str


def _consume_state(db: Session, state: str | None, binding: str | None) -> _Flow:
    """Atomic and single-use: two callbacks with one state can't both get past this."""
    if not state:
        raise SignInRejectedError("missing_state")
    row = db.execute(
        update(OAuthState)
        .where(
            OAuthState.state_hash == _sha256(state),
            OAuthState.consumed_at.is_(None),
            OAuthState.expires_at > func.now(),
        )
        .values(consumed_at=func.now())
        .returning(
            OAuthState.browser_binding_hash,
            OAuthState.nonce,
            OAuthState.pkce_verifier,
            OAuthState.locale,
            OAuthState.return_path,
        )
    ).one_or_none()
    db.commit()
    if row is None:
        raise SignInRejectedError("invalid_state")
    # Compared after consuming, so a mismatch also burns the state. This is the login-CSRF check:
    # an attacker can't make a victim's browser finish the attacker's sign-in.
    if not binding or not secrets.compare_digest(_sha256(binding), row.browser_binding_hash):
        raise SignInRejectedError("browser_mismatch", row.locale)
    return _Flow(
        nonce=row.nonce,
        pkce_verifier=row.pkce_verifier,
        locale=row.locale,
        return_path=safe_return_path(row.return_path) or "/",
    )


def abandon_sign_in(db: Session, state: str | None, binding: str | None) -> str:
    """Google reported an error, such as a cancellation. Burns the state, and returns its locale."""
    return _consume_state(db, state, binding).locale


def complete_sign_in(
    db: Session,
    provider: GoogleIdentityProvider,
    *,
    state: str | None,
    binding: str | None,
    code: str | None,
) -> SignedIn:
    """Leaves the user and identity flushed but uncommitted, for the caller to add a session."""
    flow = _consume_state(db, state, binding)
    if not code:
        raise SignInRejectedError("missing_code", flow.locale)
    try:
        id_token = provider.exchange_code(code=code, code_verifier=flow.pkce_verifier)
        claims = provider.verify_id_token(id_token, nonce=flow.nonce)
    except GoogleRejectedError as exc:
        raise _reject(db, exc.reason, flow.locale) from None
    except GoogleUnavailableError:
        raise SignInRejectedError("google_unavailable", flow.locale) from None
    user, created = resolve_user(db, claims, locale=flow.locale)
    return SignedIn(user=user, created=created, locale=flow.locale, return_path=flow.return_path)


def safe_display_name(name: str | None) -> str:
    """Drops control and formatting characters, including bidirectional overrides (SECURITY §6)."""
    if not name:
        return FALLBACK_DISPLAY_NAME
    cleaned = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf"))
    cleaned = " ".join(cleaned.split())[:100].strip()
    return cleaned or FALLBACK_DISPLAY_NAME


def _find_identity(db: Session, subject: str) -> UserIdentity | None:
    return db.scalars(
        select(UserIdentity).where(
            UserIdentity.provider == PROVIDER, UserIdentity.provider_subject == subject
        )
    ).one_or_none()


def resolve_user(db: Session, claims: GoogleClaims, *, locale: str) -> tuple[User, bool]:
    """Google's subject ID decides who this is. The email address never does.

    A new `sub` always gets a new account, even if another account has the same address: the
    address may have been reassigned. Nothing is ever linked by email.
    """
    identity = _find_identity(db, claims.subject)
    if identity is not None:
        return _sign_in_existing(db, identity, claims, locale), False

    if not claims.email_verified or claims.email is None:
        raise _reject(db, "email_not_verified", locale)
    now = datetime.now(UTC)
    try:
        with db.begin_nested():
            user = User(
                email=claims.email, display_name=safe_display_name(claims.name), locale=locale
            )
            db.add(user)
            db.flush()
            db.add(
                UserIdentity(
                    user_id=user.id,
                    provider=PROVIDER,
                    provider_subject=claims.subject,
                    provider_email=claims.email,
                    provider_email_verified=True,
                    provider_picture_url=claims.picture,
                    last_used_at=now,
                )
            )
            db.flush()
    except IntegrityError:
        # Someone else created this same Google account first, such as a double-clicked sign-in.
        # The savepoint rolled back everything half-made. Wait for theirs, and use it.
        db.rollback()
        identity = _find_identity(db, claims.subject)
        if identity is None:
            raise
        return _sign_in_existing(db, identity, claims, locale), False
    audit(db, "auth.google.account_created", user.id)
    audit(db, "auth.google.signed_in", user.id)
    return user, True


def _sign_in_existing(
    db: Session, identity: UserIdentity, claims: GoogleClaims, locale: str
) -> User:
    user = db.get(User, identity.user_id)
    assert user is not None  # noqa: S101 - guaranteed by the foreign key
    if user.status not in SIGN_IN_STATUSES:
        raise _reject(db, "account_unavailable", locale, user)
    identity.last_used_at = datetime.now(UTC)
    # The picture follows Google: changed, added, or removed there, the same here.
    identity.provider_picture_url = claims.picture
    if claims.email_verified and claims.email and claims.email != identity.provider_email:
        # The address changed at Google. Identity is unaffected; only the copies are refreshed.
        identity.provider_email = claims.email
        identity.provider_email_verified = True
        user.email = claims.email
        audit(db, "auth.google.email_changed", user.id)
    audit(db, "auth.google.signed_in", user.id)
    return user
