"""Operations endpoints (API_SPEC.md §14). They reveal no details, because they're public."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import get_settings_for_request
from app.core.config import Environment, Settings
from app.core.errors import ApiError
from app.db.session import get_engine
from app.services.readiness import is_ready

router = APIRouter(tags=["operations"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"


@router.get("/healthz")
def healthz() -> HealthResponse:
    """Liveness: the process is up. Doesn't touch the database."""
    return HealthResponse()


@router.get("/readyz")
def readyz(
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings_for_request),
) -> ReadyResponse:
    """Readiness: the database is reachable and migrated, and in production, locked down."""
    if not is_ready(engine, require_hardened=settings.app_env is Environment.PRODUCTION):
        raise ApiError(503, "not_ready", "Service not ready")
    return ReadyResponse()
