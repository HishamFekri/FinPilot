"""Transfers between a user's own accounts (FINANCIAL_RULES.md §4.4, §4.5).

Neither income nor spending. Same currency: one amount leaves and arrives. Different currencies:
the person enters both real amounts; the implied rate is shown, never invented or applied.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator
from sqlalchemy.orm import Session

from app.api.deps import require_active_user, require_writable_user
from app.api.v1.accounts import AccountOut, MoneyIn, MoneyOut, _no_store, _outs, write_rate_limit
from app.api.v1.idempotent import run_once
from app.db.models import User
from app.db.session import get_db
from app.services import money, movements
from app.services.accounts import AccountView, clean_text
from app.services.ledger import balances

router = APIRouter(prefix="/transfers", tags=["transfers"], dependencies=[Depends(_no_store)])


class TransferPreviewOut(BaseModel):
    sent: MoneyOut
    received: MoneyOut
    cross_currency: bool
    # 1 unit sent = `implied_rate` units received. A memo from the two real amounts; null within
    # one currency. Never used to convert anything.
    implied_rate: str | None


def _preview(plan: movements.TransferPlan) -> TransferPreviewOut:
    return TransferPreviewOut(
        sent=MoneyOut(
            amount=money.to_amount(plan.sent_minor, plan.sent_exponent),
            currency=plan.source.currency,
        ),
        received=MoneyOut(
            amount=money.to_amount(plan.received_minor, plan.received_exponent),
            currency=plan.destination.currency,
        ),
        cross_currency=plan.cross_currency,
        implied_rate=plan.implied_rate(),
    )


@router.get("/preview")
def transfer_preview(
    from_account_id: uuid.UUID,
    to_account_id: uuid.UUID,
    amount: Annotated[str, Query(max_length=40)],
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")],
    received_amount: Annotated[str | None, Query(max_length=40)] = None,
    received_currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> TransferPreviewOut:
    """Checks a transfer and shows its implied rate, without posting anything."""
    return _preview(
        movements.preview_transfer(
            db,
            user,
            from_account_id,
            to_account_id,
            amount,
            currency,
            received_amount,
            received_currency,
        )
    )


class TransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: MoneyIn
    # Required when the accounts' currencies differ; otherwise optional, and must equal `amount`.
    received_amount: MoneyIn | None = None
    effective_date: date | None = None
    description: StrictStr | None = None

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return clean_text(value, max_length=200)


class TransferOut(BaseModel):
    id: uuid.UUID
    effective_date: date
    description: str | None
    transfer: TransferPreviewOut
    from_account: AccountOut
    to_account: AccountOut


@router.post("", status_code=201, dependencies=[write_rate_limit])
def create_transfer(
    request: Request,
    body: TransferCreate,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        received = body.received_amount
        document, plan = movements.transfer(
            db,
            user,
            source_id=body.from_account_id,
            destination_id=body.to_account_id,
            amount=body.amount.amount,
            currency=body.amount.currency,
            received_amount=received.amount if received else None,
            received_currency=received.currency if received else None,
            effective_date=body.effective_date,
            description=body.description,
        )
        totals = balances(db, user.id, [plan.source.id, plan.destination.id])
        source, destination = _outs(
            db,
            user,
            [
                AccountView(plan.source, totals[plan.source.id]),
                AccountView(plan.destination, totals[plan.destination.id]),
            ],
        )
        out = TransferOut(
            id=document.id,
            effective_date=document.effective_date,
            description=document.description,
            transfer=_preview(plan),
            from_account=source,
            to_account=destination,
        )
        return 201, out.model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)
