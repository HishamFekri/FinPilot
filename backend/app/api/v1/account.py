"""The signed-in user's own account: profile, logout, and sessions (API_SPEC.md §3).

Every change needs a valid session, an allowed Origin, and the CSRF token. Session IDs are only
looked up together with the caller's user ID, so another user's session can't be seen or touched.
"""

import unicodedata
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlalchemy.orm import Session

from app.api.deps import (
    get_optional_session,
    get_settings_for_request,
    rate_limited,
    require_active_session,
    require_active_user,
    require_csrf,
    require_writable_user,
    verify_csrf,
)
from app.api.v1.cookies import PRIVATE_HEADERS, clear_session_cookies, private
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import Limit
from app.db.models import User
from app.db.session import get_db
from app.services import sessions
from app.services.sessions import ActiveSession


def _no_store(response: Response) -> None:
    """Personal data must never be cached. Covers routes that return a model, not a Response."""
    response.headers.update(PRIVATE_HEADERS)


router = APIRouter(prefix="/auth", tags=["account"], dependencies=[Depends(_no_store)])

WRITE_LIMIT = Limit(requests=60, window_seconds=60)
write_rate_limit = Depends(rate_limited("auth.write", WRITE_LIMIT))


class MeResponse(BaseModel):
    """Safe fields only: no session data, Google subject, or provider tokens."""

    id: uuid.UUID
    email: str | None
    display_name: str | None
    # Google's profile picture from the last sign-in: a public image URL, never a token.
    avatar_url: str | None
    locale: str
    preferred_currency: str | None
    time_zone: str
    status: str
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> "MeResponse":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            avatar_url=next(
                (i.provider_picture_url for i in user.identities if i.provider == "google"), None
            ),
            locale=user.locale,
            preferred_currency=user.preferred_currency,
            time_zone=user.time_zone,
            status=user.status,
            created_at=user.created_at,
        )


@lru_cache
def _time_zones() -> frozenset[str]:
    return frozenset(available_timezones())


class ProfileUpdate(BaseModel):
    """Only these fields. Email and the Google identity can't be changed here, or at all."""

    model_config = ConfigDict(extra="forbid")

    display_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    locale: Literal["en", "ar", "tr"] | None = None
    preferred_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    time_zone: Annotated[str, StringConstraints(max_length=64)] | None = None

    @field_validator("display_name")
    @classmethod
    def printable_name(cls, value: str | None) -> str | None:
        # Control and formatting characters, including bidirectional overrides, could spoof
        # how a name appears to other trip members (SECURITY.md §6).
        if value is not None and any(unicodedata.category(c) in ("Cc", "Cf") for c in value):
            raise ValueError("display_name contains characters that aren't allowed")
        return value

    @field_validator("time_zone")
    @classmethod
    def real_time_zone(cls, value: str | None) -> str | None:
        if value is not None and value not in _time_zones():
            raise ValueError("time_zone must be an IANA time zone, such as Europe/Istanbul")
        return value


class SessionResponse(BaseModel):
    id: uuid.UUID
    current: bool
    device_label: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


# --- Home layout (0017): a preference, saved per person.

HOME_WIDGETS = (
    "summary", "recent", "categories", "upcoming", "budgets", "goals", "templates", "links",
)  # fmt: skip
MAX_ROWS = 12
MAX_PER_ROW = 3
# Before the redesign: what those widgets became. Quick Add is now the New transaction button.
_RENAMED = {"total_balance": "summary", "month": "categories", "plans": "goals"}


class HomeLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[list[str]]
    hidden: list[str] = []

    @field_validator("rows")
    @classmethod
    def row_shape(cls, value: list[list[str]]) -> list[list[str]]:
        if len(value) > MAX_ROWS or any(not 1 <= len(row) <= MAX_PER_ROW for row in value):
            raise ValueError("Each row holds one to three widgets")
        return value


class HomeLayoutOut(BaseModel):
    # Null: the default layout.
    layout: HomeLayout | None
    widgets: list[str]


def upgrade_layout(stored: dict[str, Any]) -> dict[str, Any]:
    """A layout saved before rows existed (two columns), read as rows. Only rewritten on the next
    save. The person's order and hidden widgets are kept; Total Balance leads, as it always can."""
    if "rows" in stored:
        return stored

    def known(widgets: list[str]) -> list[str]:
        return [_RENAMED.get(w, w) for w in widgets if w != "quick_add"]

    columns = [known(column) for column in stored["columns"]]
    longest = max((len(column) for column in columns), default=0)
    rows = [[column[i] for column in columns if i < len(column)] for i in range(longest)]
    if any("summary" in row for row in rows):
        rows = [["summary"], *([w for w in row if w != "summary"] for row in rows)]
    return {"rows": [row for row in rows if row], "hidden": known(stored.get("hidden", []))}


def _check_layout(layout: HomeLayout) -> HomeLayout:
    seen = [w for row in layout.rows for w in row] + layout.hidden
    if any(w not in HOME_WIDGETS for w in seen):
        raise ApiError(422, "unknown_widget", "Unknown widget")
    if len(seen) != len(set(seen)):
        raise ApiError(422, "duplicate_widget", "Each widget appears once")
    return layout


@router.get("/me/home-layout")
def get_home_layout(user: User = Depends(require_active_user)) -> HomeLayoutOut:
    stored = user.home_layout
    return HomeLayoutOut(
        layout=HomeLayout.model_validate(upgrade_layout(stored)) if stored else None,
        widgets=list(HOME_WIDGETS),
    )


@router.put("/me/home-layout", dependencies=[write_rate_limit])
def put_home_layout(
    body: HomeLayout | None = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> HomeLayoutOut:
    """Saves the arrangement, or with no body goes back to the default. Last write wins."""
    user.home_layout = _check_layout(body).model_dump() if body is not None else None
    db.flush()
    return HomeLayoutOut(layout=body, widgets=list(HOME_WIDGETS))


@router.get("/me")
def me(user: User = Depends(require_active_user)) -> MeResponse:
    return MeResponse.of(user)


@router.patch("/me", dependencies=[write_rate_limit])
def update_me(
    changes: ProfileUpdate,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    fields = changes.model_dump(exclude_unset=True)
    if not fields:
        raise ApiError(422, "no_changes", "Nothing to update")
    if fields.get("display_name", "") is None or fields.get("locale", "") is None:
        raise ApiError(422, "validation_error", "display_name and locale can't be cleared")
    for name, value in fields.items():
        setattr(user, name, value)
    # Which fields changed is recorded, but not their values.
    sessions.audit(db, "user.profile.updated", user.id, fields=",".join(sorted(fields)))
    db.commit()
    return MeResponse.of(user)


@router.post("/logout", status_code=204, dependencies=[write_rate_limit])
def logout(
    request: Request,
    current: ActiveSession | None = Depends(get_optional_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_for_request),
) -> Response:
    """Ends this session. Safe to repeat: without a live session it just clears the cookies.

    With a live session the CSRF checks apply, so another site can't sign someone out.
    """
    if current is not None:
        verify_csrf(request, current, settings)
        sessions.revoke(db, current.session, reason="logout")
        sessions.audit(db, "auth.logout", current.user.id)
        db.commit()
    response = private(Response(status_code=204))
    clear_session_cookies(response, settings)
    return response


@router.get("/sessions")
def list_sessions(
    current: ActiveSession = Depends(require_active_session), db: Session = Depends(get_db)
) -> list[SessionResponse]:
    return [
        SessionResponse(
            id=row.id,
            current=row.id == current.session.id,
            device_label=row.device_label,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=min(row.idle_expires_at, row.absolute_expires_at),
        )
        for row in sessions.list_live(db, current.user.id)
    ]


@router.delete("/sessions/{session_id}", status_code=204, dependencies=[write_rate_limit])
def revoke_session(
    session_id: uuid.UUID,
    current: ActiveSession = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_for_request),
) -> Response:
    """Revokes one of the caller's own sessions. Anyone else's is reported as not found."""
    target = sessions.owned_session(db, current.user.id, session_id)
    if target is None:
        raise ApiError(404, "not_found", "Not found")
    sessions.revoke(db, target, reason="user_revoked")
    db.commit()
    response = private(Response(status_code=204))
    if target.id == current.session.id:
        clear_session_cookies(response, settings)
    return response


@router.delete("/sessions", status_code=204, dependencies=[write_rate_limit])
def revoke_other_sessions(
    current: ActiveSession = Depends(require_csrf), db: Session = Depends(get_db)
) -> Response:
    """Signs out everywhere else, keeping this session."""
    sessions.revoke_all(db, current.user.id, except_id=current.session.id, reason="others_revoked")
    db.commit()
    return private(Response(status_code=204))
