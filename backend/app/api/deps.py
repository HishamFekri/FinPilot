"""Reusable request dependencies: settings, sessions, users, CSRF, and rate limits.

The authenticated user always comes from a validated session, never from anything the client
sends as an ID. Future financial and trip routes use these unchanged:

    user: User = Depends(require_active_user)          # reading
    user: User = Depends(require_writable_user)        # changing anything (adds CSRF)

Ownership is enforced by scoping queries to `user.id`, so another user's records are not found
(404), rather than found and refused.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import Limit, RateLimiter
from app.db import rls
from app.db.models import User
from app.db.session import get_db
from app.services.sessions import (
    SIGN_IN_STATUSES,
    ActiveSession,
    audit,
    find_session,
    matches_csrf,
    revoke,
)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"


# New instances each time: a shared exception object would carry one request's traceback into
# another's.
def not_authenticated() -> ApiError:
    return ApiError(401, "not_authenticated", "Authentication required")


def account_unavailable() -> ApiError:
    return ApiError(403, "account_unavailable", "Account unavailable")


def csrf_failed() -> ApiError:
    return ApiError(403, "csrf_failed", "Request could not be verified")


def get_settings_for_request(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@dataclass(frozen=True)
class CookieNames:
    session: str
    csrf: str
    oauth_binding: str


def cookie_names(settings: Settings) -> CookieNames:
    # __Host- requires Secure, Path=/, and no Domain, which pins each cookie to this exact host.
    prefix = "__Host-" if settings.secure_cookies else ""
    return CookieNames(f"{prefix}fp_session", f"{prefix}fp_csrf", f"{prefix}fp_oauth")


def idle_ttl(settings: Settings) -> timedelta:
    return timedelta(hours=settings.session_idle_hours)


def absolute_ttl(settings: Settings) -> timedelta:
    return timedelta(hours=settings.session_absolute_hours)


# --- Sessions and users


def get_optional_session(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_for_request),
) -> ActiveSession | None:
    """The live session from the session cookie, or None. Browsers only, for now.

    Future mobile clients will send the same opaque token as `Authorization: Bearer`; that path
    is deliberately not accepted yet (ARCHITECTURE.md §5.3).
    """
    token = request.cookies.get(cookie_names(settings).session)
    current = find_session(db, token, idle=idle_ttl(settings))
    if current is None:
        return None
    rls.act_as(db, current.user.id)
    if current.user.status == "deleted":
        revoke(db, current.session, reason="account_deleted")
        db.commit()
        return None
    return current


def require_session(current: ActiveSession | None = Depends(get_optional_session)) -> ActiveSession:
    if current is None:
        raise not_authenticated()
    return current


def require_user(current: ActiveSession = Depends(require_session)) -> User:
    """Any signed-in user, whatever their status. For actions like viewing one's own status."""
    return current.user


def require_active_session(
    current: ActiveSession = Depends(require_session), db: Session = Depends(get_db)
) -> ActiveSession:
    """Refuses suspended accounts. The attempt is audited, and that session is ended."""
    if current.user.status not in SIGN_IN_STATUSES:
        revoke(db, current.session, reason="account_unavailable")
        audit(db, "auth.session.denied", current.user.id, reason=current.user.status)
        db.commit()
        raise account_unavailable()
    return current


def require_active_user(current: ActiveSession = Depends(require_active_session)) -> User:
    return current.user


# --- CSRF


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return origin
    # Fallback for browsers that omit Origin. Only the origin part of the Referer is used.
    referer = request.headers.get("referer")
    if referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return None


def verify_csrf(request: Request, current: ActiveSession, settings: Settings) -> None:
    """Both checks must pass for a cookie-authenticated change.

    1. Origin (or Referer) is one of FinPilot's own origins. Missing counts as a failure.
    2. The X-CSRF-Token header matches this session's token, which only FinPilot's own pages can
       read from the fp_csrf cookie. Its hash is stored with the session, compared in constant time.
    """
    if request.method not in UNSAFE_METHODS:
        return
    if _request_origin(request) not in settings.allowed_origins:
        raise csrf_failed()
    if not matches_csrf(current.session, request.headers.get(CSRF_HEADER)):
        raise csrf_failed()


def require_csrf(
    request: Request,
    current: ActiveSession = Depends(require_active_session),
    settings: Settings = Depends(get_settings_for_request),
) -> ActiveSession:
    verify_csrf(request, current, settings)
    return current


def require_writable_user(current: ActiveSession = Depends(require_csrf)) -> User:
    """Financial changes stop while a deletion is pending: reading and exporting don't."""
    if current.user.status == "deletion_pending":
        raise ApiError(409, "deletion_pending", "This account is waiting to be deleted")
    return current.user


# --- Rate limits


def client_key(request: Request) -> str:
    """The direct peer address, for callers with no session. Behind a proxy this needs trusted-proxy
    handling (uvicorn's --forwarded-allow-ips), or every visitor shares one bucket (PD3)."""
    return request.client.host if request.client else "unknown"


def rate_limited(bucket: str, limit: Limit) -> Callable[[Request], None]:
    """Counts per signed-in person, or per client address before signing in.

    Per person so that visitors behind one proxy can't exhaust each other's allowance, and only for
    a session that really exists, so inventing cookies can't buy new buckets. The key and address
    are never logged or returned.
    """

    def check(
        request: Request, current: ActiveSession | None = Depends(get_optional_session)
    ) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        who = f"user:{current.user.id}" if current else f"ip:{client_key(request)}"
        retry_after = limiter.hit(f"{bucket}:{who}", limit)
        if retry_after is not None:
            raise ApiError(
                429, "rate_limited", "Too many requests", headers={"Retry-After": str(retry_after)}
            )

    check.__name__ = f"rate_limit_{bucket.replace('.', '_')}"
    return check
