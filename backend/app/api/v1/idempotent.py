"""Runs a financial write at most once per `Idempotency-Key` (FINANCIAL_RULES.md §14.1).

The key, the operation, and the stored response share the request's database transaction: a
failure rolls all three back, so a retry runs cleanly; a success replays byte-for-byte.
"""

from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.v1.cookies import PRIVATE_HEADERS
from app.db.models import User
from app.services import idempotency


def run_once(
    db: Session,
    user: User,
    request: Request,
    key_header: str | None,
    body: Any,
    operation: Callable[[], tuple[int, Any]],
) -> JSONResponse:
    key = idempotency.parse_key(key_header)
    digest = idempotency.request_hash(request.method, request.url.path, body)
    replay = idempotency.claim(db, user.id, key, digest)
    if replay is not None:
        return JSONResponse(
            replay.body,
            status_code=replay.status,
            headers={**PRIVATE_HEADERS, "Idempotent-Replayed": "true"},
        )
    status, payload = operation()
    idempotency.store(db, user.id, key, status, payload)
    return JSONResponse(payload, status_code=status, headers=PRIVATE_HEADERS)
