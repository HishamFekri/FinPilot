"""Idempotent financial writes (FINANCIAL_RULES.md §14.1, API_SPEC.md §1.6).

The client sends `Idempotency-Key: <uuid>`. The key is claimed in the *same database transaction*
as the operation:

- New key: claimed, the operation runs, and its response is stored on the key before commit.
- Same key, same request: the stored response is replayed. Nothing happens twice.
- Same key, different request: 422 `idempotency_key_reused`.

Two concurrent requests with one key can't both run: PostgreSQL makes the second insert wait for
the first transaction. If that one commits, the second replays it; if it rolls back, the key is
gone too, and the second runs normally.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import IdempotencyKey


@dataclass(frozen=True)
class Replay:
    status: int
    body: Any


def request_hash(method: str, path: str, body: Any) -> bytes:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{method} {path}\n{canonical}".encode()).digest()


def parse_key(value: str | None) -> uuid.UUID:
    if not value:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header is required")
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key must be a UUID") from None


def claim(db: Session, user_id: uuid.UUID, key: uuid.UUID, digest: bytes) -> Replay | None:
    """Claims the key for this request, or returns the earlier response to replay."""
    claimed = db.execute(
        insert(IdempotencyKey)
        .values(user_id=user_id, key=key, request_hash=digest, response_status=0, response_body={})
        .on_conflict_do_nothing(index_elements=["user_id", "key"])
        .returning(IdempotencyKey.key)
    ).first()
    if claimed is not None:
        return None
    row = db.execute(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
    ).scalar_one()
    if row.request_hash != digest:
        raise ApiError(
            422, "idempotency_key_reused", "Idempotency-Key was already used for another request"
        )
    return Replay(row.response_status, row.response_body)


def store(db: Session, user_id: uuid.UUID, key: uuid.UUID, status: int, body: Any) -> None:
    """Records the response on the claimed key, in the operation's transaction."""
    db.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
        .values(response_status=status, response_body=body)
    )
