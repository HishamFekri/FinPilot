"""Cookie and caching rules for authentication responses, in one place.

| Cookie       | HttpOnly | Purpose                                                    |
|--------------|----------|------------------------------------------------------------|
| fp_session   | yes      | The opaque session token. JavaScript can never read it      |
| fp_csrf      | no       | Read by FinPilot's own pages, sent back as X-CSRF-Token     |
| fp_oauth     | yes      | Binds a Google sign-in to the browser that started it       |

All are SameSite=Lax and Path=/. Outside local development they're Secure and use the __Host-
prefix, which forbids a Domain attribute, so no other host or subdomain can set or read them.
"""

from fastapi import Response

from app.api.deps import cookie_names
from app.core.config import Settings
from app.services.sessions import IssuedSession

PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


def private[R: Response](response: R) -> R:
    response.headers.update(PRIVATE_HEADERS)
    return response


def _set(response: Response, settings: Settings, name: str, value: str, *, httponly: bool) -> None:
    response.set_cookie(
        name,
        value,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
        secure=settings.secure_cookies,
        httponly=httponly,
        samesite="lax",
    )


def _clear(response: Response, settings: Settings, name: str, *, httponly: bool) -> None:
    response.delete_cookie(
        name, path="/", secure=settings.secure_cookies, httponly=httponly, samesite="lax"
    )


def set_session_cookies(response: Response, settings: Settings, issued: IssuedSession) -> None:
    names = cookie_names(settings)
    _set(response, settings, names.session, issued.token, httponly=True)
    _set(response, settings, names.csrf, issued.csrf_token, httponly=False)


def clear_session_cookies(response: Response, settings: Settings) -> None:
    names = cookie_names(settings)
    _clear(response, settings, names.session, httponly=True)
    _clear(response, settings, names.csrf, httponly=False)


def clear_oauth_binding(response: Response, settings: Settings) -> None:
    _clear(response, settings, cookie_names(settings).oauth_binding, httponly=True)
