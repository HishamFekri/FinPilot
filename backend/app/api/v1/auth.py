"""Google sign-in routes (API_SPEC.md §2). Google is the only way to authenticate.

The callback ends with a FinPilot session cookie and a redirect to the web app. Failures redirect to
the public landing page with a fixed error code. Neither carries a token, code, or provider
message. Query strings aren't logged anywhere (core/middleware.py, core/logging.py), which matters
here because the callback URL holds Google's code.
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import (
    absolute_ttl,
    cookie_names,
    get_settings_for_request,
    idle_ttl,
    rate_limited,
)
from app.api.v1.cookies import clear_oauth_binding, private, set_session_cookies
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import Limit
from app.db.session import get_db
from app.services.google_oidc import GoogleIdentityProvider, GoogleUnavailableError
from app.services.google_signin import (
    SignInRejectedError,
    abandon_sign_in,
    complete_sign_in,
    safe_return_path,
    start_sign_in,
)
from app.services.sessions import issue_session

logger = logging.getLogger(__name__)

START_LIMIT = Limit(requests=20, window_seconds=600)
CALLBACK_LIMIT = Limit(requests=30, window_seconds=600)
# Codes the web app may show, translated. Which check failed is logged and audited, never sent.
ERROR_CODES = {
    "cancelled": "cancelled",
    "email_not_verified": "email_not_verified",
    "account_unavailable": "account_unavailable",
    "registration_closed": "registration_closed",
    "google_unavailable": "google_unavailable",
    "unreachable": "unreachable",
}
LOCALES = ("en", "ar", "tr")
# The web app's language cookie (apps/web/src/i18n/routing.ts).
LOCALE_COOKIE = "fp_locale"


def get_google_provider(request: Request) -> GoogleIdentityProvider:
    provider: GoogleIdentityProvider | None = request.app.state.google_provider
    if provider is None:
        raise ApiError(503, "google_sign_in_not_configured", "Google sign-in isn't configured")
    return provider


def _failure(
    request: Request, settings: Settings, reason: str, locale: str | None = None
) -> RedirectResponse:
    """Back to the landing page, in the person's language, with a fixed code it can translate."""
    if locale not in LOCALES:
        # No sign-in attempt to read it from: use the language the browser last chose.
        cookie = request.cookies.get(LOCALE_COOKIE)
        locale = cookie if cookie in LOCALES else "en"
    code = ERROR_CODES.get(reason, "failed")
    target = f"{settings.normalized_web_origin}/{locale}?error={quote(code)}"
    response = private(RedirectResponse(target, status_code=303))
    clear_oauth_binding(response, settings)
    return response


class _SignInRouteFailed(Exception):
    """Raised, not returned, so it passes through `get_db` and the transaction rolls back."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _BrowserRoute(APIRoute):
    """Start and callback are page loads, so a failure must end on a page, never on raw JSON.

    Covers what happens outside the handlers: the rate limit, an unconfigured or unreachable
    Google, and anything unexpected, such as the database being down. A malformed request (422)
    is not something the web app can send, and stays a plain error.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            try:
                return await handler(request)
            except (RequestValidationError, StarletteHTTPException):
                raise
            except ApiError as exc:
                if exc.status not in (429, 503):
                    raise
                reason = "google_unavailable" if exc.status == 503 else "failed"
                raise _SignInRouteFailed(reason) from exc
            except Exception as exc:
                # As in core/errors.py: details stay in the logs, the browser gets a fixed code.
                logger.error(
                    "Sign-in route failed", exc_info=exc, extra={"error_type": type(exc).__name__}
                )
                raise _SignInRouteFailed("unreachable") from exc

        return run


async def _handle_route_failure(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, _SignInRouteFailed)  # noqa: S101 - narrowing only
    locale = request.query_params.get("locale")
    return _failure(request, request.app.state.settings, exc.reason, locale)


def register_sign_in_errors(app: FastAPI) -> None:
    app.add_exception_handler(_SignInRouteFailed, _handle_route_failure)


router = APIRouter(prefix="/auth/google", tags=["authentication"], route_class=_BrowserRoute)


@router.get(
    "/start",
    status_code=302,
    dependencies=[Depends(rate_limited("auth.google.start", START_LIMIT))],
)
def start(
    locale: Literal["en", "ar", "tr"] = "en",
    return_to: Annotated[str | None, Query(max_length=200)] = None,
    db: Session = Depends(get_db),
    provider: GoogleIdentityProvider = Depends(get_google_provider),
    settings: Settings = Depends(get_settings_for_request),
) -> RedirectResponse:
    """Sends the browser to Google.

    `locale` sets a new account's language. `return_to` is a path on the web app to land on
    afterwards. Anything that isn't a plain local path is refused, so this can't become an open
    redirect.
    """
    return_path = safe_return_path(return_to) if return_to is not None else f"/{locale}"
    if return_path is None:
        raise ApiError(422, "invalid_return_path", "return_to must be a path on this site")
    try:
        started = start_sign_in(
            db,
            provider,
            locale=locale,
            return_path=return_path,
            ttl_seconds=settings.oauth_state_ttl_seconds,
        )
    except GoogleUnavailableError:
        raise ApiError(503, "google_unavailable", "Google sign-in is unavailable") from None
    response = private(RedirectResponse(started.authorization_url, status_code=302))
    response.set_cookie(
        cookie_names(settings).oauth_binding,
        started.browser_binding,
        max_age=settings.oauth_state_ttl_seconds,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        # Lax, not Strict: the return from Google is a cross-site top-level navigation.
        samesite="lax",
    )
    return response


@router.get(
    "/callback",
    response_model=None,
    dependencies=[Depends(rate_limited("auth.google.callback", CALLBACK_LIMIT))],
)
def callback(
    request: Request,
    state: Annotated[str | None, Query(max_length=512)] = None,
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
    db: Session = Depends(get_db),
    provider: GoogleIdentityProvider = Depends(get_google_provider),
    settings: Settings = Depends(get_settings_for_request),
) -> RedirectResponse:
    """Google redirects here. `error_description` is ignored, so its text is never shown."""
    names = cookie_names(settings)
    binding = request.cookies.get(names.oauth_binding)
    try:
        if error is not None:
            locale = abandon_sign_in(db, state, binding)
            reason = "cancelled" if error == "access_denied" else "failed"
            return _failure(request, settings, reason, locale)
        signed_in = complete_sign_in(
            db,
            provider,
            state=state,
            binding=binding,
            code=code,
            admit=settings.admits_new_account,
        )
        issued = issue_session(
            db,
            signed_in.user,
            idle=idle_ttl(settings),
            absolute=absolute_ttl(settings),
            user_agent=request.headers.get("user-agent"),
            replacing_token=request.cookies.get(names.session),
        )
        # Committed before any cookie is set: if this fails, there's no user, identity, or
        # session, and the browser gets no cookie.
        db.commit()
    except SignInRejectedError as exc:
        return _failure(request, settings, exc.reason, exc.locale)

    response = private(
        RedirectResponse(
            f"{settings.normalized_web_origin}{signed_in.return_path}", status_code=303
        )
    )
    clear_oauth_binding(response, settings)
    set_session_cookies(response, settings, issued)
    return response
