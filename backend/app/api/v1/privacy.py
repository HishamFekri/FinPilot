"""The signed-in person's own data: a full download, and asking to delete the account.

Every route works from the session's user, never from an ID in the request. Requesting deletion
only marks the account as pending, and the person can undo that. Nothing here erases anything.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    get_settings_for_request,
    rate_limited,
    require_active_session,
    require_active_user,
    require_csrf,
)
from app.api.v1.cookies import PRIVATE_HEADERS
from app.core.config import Settings
from app.core.rate_limit import Limit
from app.db.models import User
from app.db.session import get_db
from app.services import account_data, account_deletion
from app.services.sessions import ActiveSession


def _no_store(response: Response) -> None:
    response.headers.update(PRIVATE_HEADERS)


router = APIRouter(prefix="/me", tags=["privacy"], dependencies=[Depends(_no_store)])

export_limit = Depends(rate_limited("privacy.export", Limit(requests=5, window_seconds=3600)))
read_limit = Depends(rate_limited("privacy.read", Limit(requests=60, window_seconds=60)))
write_limit = Depends(rate_limited("privacy.write", Limit(requests=10, window_seconds=3600)))


class BlockerOut(BaseModel):
    kind: Literal["trip_balance", "trip_pending", "trip_dispute", "trip_owner"]
    trip_id: uuid.UUID
    trip_name: str
    amount_minor: int | None
    currency: str | None


class WarningOut(BaseModel):
    kind: Literal["personal_debts", "installment_plans"]
    count: int


class DeletionStatus(BaseModel):
    status: str
    requested_at: datetime | None
    # False: the person has to sign in with Google again before confirming.
    recent_sign_in: bool
    can_request: bool
    blockers: list[BlockerOut]
    warnings: list[WarningOut]
    shared_expenses_kept: int


class DeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_email: str = Field(min_length=3, max_length=254)
    # They have to have seen what stays behind and what happens to their debts.
    acknowledge_retention: Literal[True]


def _status(db: Session, current: ActiveSession) -> DeletionStatus:
    user = current.user
    result = account_deletion.eligibility(db, user)
    return DeletionStatus(
        status=user.status,
        requested_at=user.deletion_requested_at,
        recent_sign_in=account_deletion.recently_signed_in(current.session),
        can_request=user.status == "active" and not result.blockers,
        blockers=[BlockerOut.model_validate(vars(b)) for b in result.blockers],
        warnings=[WarningOut.model_validate(vars(w)) for w in result.warnings],
        shared_expenses_kept=account_deletion.share_count(db, user.id),
    )


@router.get("/data-export", dependencies=[export_limit])
def data_export(
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_for_request),
) -> Response:
    """A JSON file of everything that's the person's own. Allowed during a deletion request."""
    data = account_data.build(db, user, limit=settings.data_export_max_rows)
    name = account_data.filename(datetime.now(UTC).date())
    return Response(
        json.dumps(data, ensure_ascii=False, indent=1).encode(),
        media_type="application/json",
        headers={
            **PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Export-Complete": "true" if data["complete"] else "false",
        },
    )


@router.get("/deletion-request", dependencies=[read_limit])
def deletion_status(
    current: ActiveSession = Depends(require_active_session), db: Session = Depends(get_db)
) -> DeletionStatus:
    return _status(db, current)


@router.post("/deletion-request", dependencies=[write_limit])
def request_deletion(
    body: DeletionRequest,
    current: ActiveSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> DeletionStatus:
    account_deletion.request_deletion(
        db, current.user, current.session, confirm_email=body.confirm_email
    )
    db.commit()
    return _status(db, current)


@router.delete("/deletion-request", dependencies=[write_limit])
def cancel_deletion(
    current: ActiveSession = Depends(require_csrf), db: Session = Depends(get_db)
) -> DeletionStatus:
    account_deletion.cancel_deletion(db, current.user)
    db.commit()
    return _status(db, current)
