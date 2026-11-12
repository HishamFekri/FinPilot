"""FinPilot's own sessions: opaque random tokens, stored only as SHA-256 hashes.

Google tokens are never sessions. A session belongs to exactly one user, can be revoked one at a
time, and stops working on the next request after revocation, expiry, or suspension.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.db.models import AuditEvent, User, UserSession

TOKEN_BYTES = 32
TOUCH_INTERVAL = timedelta(minutes=1)
# Revoked or expired rows are kept this long, for the session list and investigations.
RETENTION = timedelta(days=30)
CLEANUP_BATCH = 500
SIGN_IN_STATUSES = frozenset({"active", "deletion_pending"})


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True)
class IssuedSession:
    """The only place raw tokens exist. They go into cookies, and nowhere else."""

    session_id: uuid.UUID
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    absolute_expires_at: datetime


def audit(
    db: Session,
    action: str,
    user_id: uuid.UUID | None,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    **metadata: str,
) -> None:
    """Written in the caller's transaction. If it fails, the change it describes rolls back too."""
    db.add(
        AuditEvent(
            action=action,
            scope="user" if user_id else "system",
            owner_user_id=user_id,
            actor_user_id=user_id,
            entity_type=entity_type or ("user" if user_id else None),
            entity_id=entity_id or user_id,
            event_metadata=metadata,
            request_id=request_id_var.get(),
        )
    )


_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("Windows", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


def device_label(user_agent: str | None) -> str | None:
    """A coarse label such as "Firefox on Windows". The raw User-Agent is never stored."""
    if not user_agent:
        return None
    browser = next((name for marker, name in _BROWSERS if marker in user_agent), None)
    system = next((name for marker, name in _SYSTEMS if marker in user_agent), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system


def issue_session(
    db: Session,
    user: User,
    *,
    idle: timedelta,
    absolute: timedelta,
    user_agent: str | None,
    replacing_token: str | None = None,
) -> IssuedSession:
    """A new session for a web browser, in the caller's transaction.

    Always a new token, so no session ID chosen beforehand can be fixed onto the user. Any session
    the browser already presented is revoked: signing in rotates it.
    """
    if user.status not in SIGN_IN_STATUSES:
        raise ValueError("Refusing to issue a session for an account that can't sign in")
    if replacing_token:
        previous = db.scalars(
            select(UserSession).where(
                UserSession.token_hash == hash_token(replacing_token),
                UserSession.revoked_at.is_(None),
            )
        ).one_or_none()
        if previous is not None:
            previous.revoked_at = func.now()
            audit(
                db,
                "auth.session.revoked",
                previous.user_id,
                entity_type="session",
                entity_id=previous.id,
                reason="rotated",
            )
    _cleanup(db)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    csrf_token = secrets.token_urlsafe(TOKEN_BYTES)
    now = datetime.now(UTC)
    row = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_secret=hash_token(csrf_token),
        client_type="web",
        device_label=device_label(user_agent),
        idle_expires_at=now + min(idle, absolute),
        absolute_expires_at=now + absolute,
    )
    db.add(row)
    db.flush()
    audit(db, "auth.session.created", user.id, entity_type="session", entity_id=row.id)
    return IssuedSession(
        session_id=row.id,
        token=token,
        csrf_token=csrf_token,
        absolute_expires_at=row.absolute_expires_at,
    )


def _cleanup(db: Session) -> None:
    """Bounded deletion of long-dead sessions, done whenever a session is issued."""
    cutoff = datetime.now(UTC) - RETENTION
    dead = (
        select(UserSession.id)
        .where((UserSession.absolute_expires_at < cutoff) | (UserSession.revoked_at < cutoff))
        .limit(CLEANUP_BATCH)
    )
    db.execute(delete(UserSession).where(UserSession.id.in_(dead)))


@dataclass(frozen=True)
class ActiveSession:
    session: UserSession
    user: User


def find_session(db: Session, token: str | None, *, idle: timedelta) -> ActiveSession | None:
    """The live session for this token, or None. Never says why a token failed."""
    if not token or len(token) > 128:
        return None
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == hash_token(token),
            UserSession.revoked_at.is_(None),
            UserSession.idle_expires_at > func.now(),
            UserSession.absolute_expires_at > func.now(),
        )
    ).one_or_none()
    if row is None:
        return None
    current, user = row
    _touch(current, idle=idle)
    return ActiveSession(session=current, user=user)


def _touch(current: UserSession, *, idle: timedelta) -> None:
    """Slides the idle expiry, at most once a minute, and never past the absolute expiry."""
    now = datetime.now(UTC)
    if now - current.last_seen_at < TOUCH_INTERVAL:
        return
    current.last_seen_at = now
    current.idle_expires_at = min(now + idle, current.absolute_expires_at)


def matches_csrf(current: UserSession, csrf_token: str | None) -> bool:
    if not csrf_token or current.csrf_secret is None or len(csrf_token) > 128:
        return False
    return secrets.compare_digest(hash_token(csrf_token), current.csrf_secret)


def revoke(db: Session, current: UserSession, *, reason: str) -> None:
    if current.revoked_at is None:
        current.revoked_at = func.now()
        audit(
            db,
            "auth.session.revoked",
            current.user_id,
            entity_type="session",
            entity_id=current.id,
            reason=reason,
        )


def revoke_all(db: Session, user_id: uuid.UUID, *, except_id: uuid.UUID | None, reason: str) -> int:
    query = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_id is not None:
        query = query.where(UserSession.id != except_id)
    count = db.execute(query.values(revoked_at=func.now())).rowcount  # type: ignore[attr-defined]
    if count:
        audit(db, "auth.session.revoked", user_id, reason=reason, count=str(count))
    return int(count)


def list_live(db: Session, user_id: uuid.UUID) -> list[UserSession]:
    """Only this user's sessions, only live ones, newest first."""
    return list(
        db.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.idle_expires_at > func.now(),
                UserSession.absolute_expires_at > func.now(),
            )
            .order_by(UserSession.created_at.desc())
        )
    )


def owned_session(db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> UserSession | None:
    """Scoped by owner in the query itself, so another user's session is simply not found."""
    return db.scalars(
        select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user_id)
    ).one_or_none()
