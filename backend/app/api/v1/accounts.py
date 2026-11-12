"""Financial accounts (API_SPEC.md §4). Balances are derived from the ledger, never set directly.

Every route needs an active session; every change also needs CSRF. Account IDs are only ever looked
up together with the session's user, so another user's account is a 404.
"""

import json
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import rate_limited, require_active_user, require_writable_user
from app.api.v1.cookies import PRIVATE_HEADERS
from app.api.v1.idempotent import run_once
from app.core.errors import ApiError
from app.core.rate_limit import Limit
from app.db.models import Currency, User
from app.db.session import get_db
from app.services import accounts, money, movements, setup
from app.services.accounts import AccountView, NewAccount, clean_text


def _no_store(response: Response) -> None:
    response.headers.update(PRIVATE_HEADERS)


router = APIRouter(prefix="/accounts", tags=["accounts"], dependencies=[Depends(_no_store)])
write_rate_limit = Depends(rate_limited("finance.write", Limit(requests=120, window_seconds=60)))

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
AccountType = Literal["cash", "bank", "savings", "wallet", "other"]


class MoneyIn(BaseModel):
    """`{"amount": "1250.50", "currency": "TRY"}`. A JSON number is refused (M2)."""

    model_config = ConfigDict(extra="forbid")

    amount: StrictStr
    currency: CurrencyCode


class MoneyOut(BaseModel):
    amount: str
    currency: str


def _name(value: str) -> str:
    return clean_text(value, max_length=100)


def _description(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return clean_text(value, max_length=500)


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr
    description: StrictStr | None = None
    type: AccountType
    currency: CurrencyCode
    opened_on: date | None = None
    opening_balance: MoneyIn | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _name(value)

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        return _description(value)


class AccountUpdate(BaseModel):
    """Name and description only. Type and currency never change."""

    model_config = ConfigDict(extra="forbid")

    name: StrictStr | None = None
    description: StrictStr | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("name can't be cleared")
        return _name(value)

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        return _description(value)


class AccountOut(BaseModel):
    id: uuid.UUID
    # Null for the automatic Cash and Bank accounts until renamed: show `system_key`'s
    # translation instead.
    name: str | None
    system_key: str | None
    is_default: bool
    description: str | None
    type: str
    currency: str
    balance: MoneyOut
    status: str
    opened_on: date
    version: int


def _out(view: AccountView, exponent: int, default_id: uuid.UUID | None) -> AccountOut:
    a = view.account
    return AccountOut(
        id=a.id,
        name=a.name,
        system_key=a.system_key,
        is_default=a.id == default_id,
        description=a.description,
        type=a.type,
        currency=a.currency,
        balance=MoneyOut(amount=money.to_amount(view.balance_minor, exponent), currency=a.currency),
        status=a.status,
        opened_on=a.opened_on,
        version=a.version,
    )


def _outs(db: Session, user: User, views: list[AccountView]) -> list[AccountOut]:
    exponents = money.exponents(db, {v.account.currency for v in views})
    default = setup.default_account(db, user)
    default_id = default.id if default else None
    return [_out(v, exponents[v.account.currency], default_id) for v in views]


def _with_etag(response: Response, account: AccountOut) -> AccountOut:
    response.headers["ETag"] = f'"{account.version}"'
    return account


def _if_match(value: str | None) -> int:
    if value is None:
        raise ApiError(
            428, "precondition_required", "If-Match with the account version is required"
        )
    try:
        return int(value.strip().removeprefix("W/").strip('"'))
    except ValueError:
        raise ApiError(400, "invalid_if_match", "If-Match must be the account version") from None


@router.get("")
def list_accounts(
    status: Annotated[Literal["active", "archived"] | None, Query()] = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[AccountOut]:
    return _outs(db, user, accounts.list_for(db, user, status))


@router.post("", status_code=201, dependencies=[write_rate_limit], response_model=AccountOut)
def create_account(
    request: Request,
    body: AccountCreate,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        opening = body.opening_balance
        view = accounts.create(
            db,
            user,
            NewAccount(
                name=body.name,
                description=body.description,
                type=body.type,
                currency=body.currency,
                opened_on=body.opened_on,
                opening_amount=opening.amount if opening else None,
                opening_currency=opening.currency if opening else None,
            ),
        )
        return 201, _outs(db, user, [view])[0].model_dump(mode="json")

    response = run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)
    response.headers["ETag"] = f'"{json.loads(bytes(response.body))["version"]}"'
    return response


class CurrencyTotalOut(BaseModel):
    """Active accounts' balances added up within one currency. Never across currencies."""

    balance: MoneyOut
    accounts: int


@router.get("/totals")
def account_totals(
    user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[CurrencyTotalOut]:
    rows = movements.totals(db, user)
    exponents = money.exponents(db, {r.currency for r in rows})
    return [
        CurrencyTotalOut(
            balance=MoneyOut(
                amount=money.to_amount(r.amount_minor, exponents[r.currency]), currency=r.currency
            ),
            accounts=r.accounts,
        )
        for r in rows
    ]


@router.get("/{account_id}")
def get_account(
    account_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    return _with_etag(response, _outs(db, user, [accounts.get(db, user, account_id)])[0])


@router.patch("/{account_id}", dependencies=[write_rate_limit])
def update_account(
    account_id: uuid.UUID,
    body: AccountUpdate,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "no_changes", "Nothing to update")
    view = accounts.rename(db, user, account_id, _if_match(if_match), changes)
    return _with_etag(response, _outs(db, user, [view])[0])


@router.post("/{account_id}/archive", dependencies=[write_rate_limit])
def archive_account(
    account_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    return _with_etag(response, _outs(db, user, [accounts.archive(db, user, account_id)])[0])


@router.post("/{account_id}/unarchive", dependencies=[write_rate_limit])
def unarchive_account(
    account_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    return _with_etag(response, _outs(db, user, [accounts.unarchive(db, user, account_id)])[0])


@router.post("/{account_id}/make-default", dependencies=[write_rate_limit])
def make_default(
    account_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    """Quick Add starts with this account from now on."""
    setup.set_default(db, user, account_id)
    return _with_etag(response, _outs(db, user, [accounts.get(db, user, account_id)])[0])


@router.delete("/{account_id}", status_code=204, dependencies=[write_rate_limit])
def delete_account(
    account_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Response:
    accounts.delete(db, user, account_id)
    return Response(status_code=204, headers=PRIVATE_HEADERS)


class CurrencyOut(BaseModel):
    code: str
    exponent: int


currencies_router = APIRouter(
    prefix="/currencies", tags=["accounts"], dependencies=[Depends(_no_store)]
)


@currencies_router.get("")
def list_currencies(
    user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[CurrencyOut]:
    """Currencies new records may use, with their decimal places, for the web app's inputs."""
    rows = db.execute(
        select(Currency.code, Currency.exponent).where(Currency.is_active).order_by(Currency.code)
    )
    return [CurrencyOut(code=code, exponent=exponent) for code, exponent in rows]


# --- Activity and adjustments


class ActivityOut(BaseModel):
    """One entry, signed from this account's side: money in is positive, money out negative."""

    id: uuid.UUID
    type: str
    effective_date: date
    amount: MoneyOut
    other_account_id: uuid.UUID | None
    category_id: uuid.UUID | None
    description: str | None
    notes: str | None
    recorded_at: datetime


class ActivityPage(BaseModel):
    items: list[ActivityOut]
    has_more: bool


@router.get("/{account_id}/activity")
def account_activity(
    account_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> ActivityPage:
    account, items, has_more = movements.activity(db, user, account_id, limit=limit)
    exponent = money.exponents(db, {account.currency})[account.currency]
    return ActivityPage(
        items=[
            ActivityOut(
                id=item.transaction.id,
                type=item.transaction.type,
                effective_date=item.transaction.effective_date,
                amount=MoneyOut(
                    amount=money.to_amount(item.amount_minor, exponent), currency=account.currency
                ),
                other_account_id=item.other_account_id,
                category_id=item.transaction.category_id,
                description=item.transaction.description,
                notes=item.transaction.notes,
                recorded_at=item.transaction.created_at,
            )
            for item in items
        ],
        has_more=has_more,
    )


class AdjustmentPreviewOut(BaseModel):
    current: MoneyOut
    target: MoneyOut
    difference: MoneyOut
    direction: Literal["increase", "decrease", "none"]


def _preview_out(db: Session, plan: movements.AdjustmentPreview) -> AdjustmentPreviewOut:
    code = plan.account.currency
    exponent = money.exponents(db, {code})[code]

    def out(minor: int) -> MoneyOut:
        return MoneyOut(amount=money.to_amount(minor, exponent), currency=code)

    difference = plan.difference_minor
    return AdjustmentPreviewOut(
        current=out(plan.current_minor),
        target=out(plan.target_minor),
        difference=out(abs(difference)),
        direction="increase" if difference > 0 else "decrease" if difference < 0 else "none",
    )


@router.get("/{account_id}/adjustment-preview")
def adjustment_preview(
    account_id: uuid.UUID,
    target: Annotated[str, Query(max_length=40)],
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")],
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> AdjustmentPreviewOut:
    """The difference an adjustment would post, worked out by the backend (M5)."""
    return _preview_out(db, movements.preview_adjustment(db, user, account_id, target, currency))


class AdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_balance: MoneyIn
    # The balance the person saw. If it changed since, the adjustment is refused (409).
    expected_balance: MoneyIn
    reason: StrictStr | None = None

    @field_validator("reason")
    @classmethod
    def check_reason(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return clean_text(value, max_length=500)


class AdjustmentOut(BaseModel):
    id: uuid.UUID
    difference: MoneyOut
    direction: Literal["increase", "decrease"]
    account: AccountOut


@router.post("/{account_id}/adjustments", status_code=201, dependencies=[write_rate_limit])
def adjust_balance(
    account_id: uuid.UUID,
    request: Request,
    body: AdjustmentCreate,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    if body.expected_balance.currency != body.target_balance.currency:
        raise ApiError(422, "currency_mismatch", "Both balances are in the account's currency")

    def operation() -> tuple[int, Any]:
        posted, view = movements.adjust(
            db,
            user,
            account_id,
            target=body.target_balance.amount,
            expected=body.expected_balance.amount,
            currency=body.target_balance.currency,
            reason=body.reason,
        )
        account = _outs(db, user, [view])[0]
        exponent = money.exponents(db, {posted.currency})[posted.currency]
        out = AdjustmentOut(
            id=posted.id,
            difference=MoneyOut(
                amount=money.to_amount(posted.amount_minor, exponent), currency=posted.currency
            ),
            direction="increase" if posted.direction == "increase" else "decrease",
            account=account,
        )
        return 201, out.model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)
