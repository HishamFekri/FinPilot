"""Trip plans, the member's private budget, and trip analytics (FINANCIAL_RULES.md §11.12).

Plans are shared with current members; estimates are never spending. The personal budget is the
caller's own and nobody else's: there is no route that reads another member's.
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy.orm import Session

from app.api.deps import require_active_user, require_writable_user
from app.api.v1.accounts import MoneyOut, _if_match, _no_store, write_rate_limit
from app.db.models import TripPlanItem, User
from app.db.session import get_db
from app.services import money, trip_planning, trips

router = APIRouter(tags=["trips"], dependencies=[Depends(_no_store)])


def _money(db: Session, minor: int | None, currency: str) -> MoneyOut | None:
    if minor is None:
        return None
    exponent = money.currency(db, currency, for_new_records=False).exponent
    return MoneyOut(amount=money.to_amount(minor, exponent), currency=currency)


def _m(db: Session, minor: int, currency: str) -> MoneyOut:
    out = _money(db, minor, currency)
    assert out is not None  # noqa: S101 - minor is an int
    return out


def _access(db: Session, user: User, trip_id: uuid.UUID, *, lock: bool = False) -> trips.Access:
    a = trips.access(db, user, trip_id, lock=lock)
    trips.require_active(a)  # plans and budgets are for current members only
    return a


class PlanItemOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    transport_mode: str | None
    starts_on: date | None
    ends_on: date | None
    booking_status: str
    # Estimates and deposits: planning only, never spending.
    estimated: MoneyOut | None
    deposit: MoneyOut | None
    reference: str | None
    url: str | None
    notes: str | None
    expense_id: uuid.UUID | None
    created_by: uuid.UUID
    can_edit: bool
    version: int


def _item_out(db: Session, a: trips.Access, user: User, i: TripPlanItem) -> PlanItemOut:
    code = a.trip.base_currency
    return PlanItemOut(
        id=i.id, kind=i.kind, title=i.title, transport_mode=i.transport_mode,
        starts_on=i.starts_on, ends_on=i.ends_on, booking_status=i.booking_status,
        estimated=_money(db, i.estimated_minor, code), deposit=_money(db, i.deposit_minor, code),
        reference=i.reference, url=i.url, notes=i.notes, expense_id=i.expense_id,
        created_by=i.created_by, can_edit=i.created_by == user.id or a.admin, version=i.version,
    )  # fmt: skip


class PlanItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["accommodation", "transport", "activity", "note", "link"] | None = None
    title: StrictStr | None = Field(default=None, max_length=100)
    transport_mode: Literal["flight", "train", "bus", "car", "ferry", "other"] | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    booking_status: Literal["idea", "planned", "booked", "cancelled"] | None = None
    estimated: StrictStr | None = None
    deposit: StrictStr | None = None
    reference: StrictStr | None = Field(default=None, max_length=60)
    url: StrictStr | None = Field(default=None, max_length=500)
    notes: StrictStr | None = Field(default=None, max_length=1000)
    expense_id: uuid.UUID | None = None


@router.get("/trips/{trip_id}/plan")
def list_plan(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[PlanItemOut]:
    a = _access(db, user, trip_id)
    return [_item_out(db, a, user, i) for i in trip_planning.items(db, a)]


@router.post("/trips/{trip_id}/plan", status_code=201, dependencies=[write_rate_limit])
def add_plan_item(
    trip_id: uuid.UUID,
    body: PlanItemIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> PlanItemOut:
    a = _access(db, user, trip_id, lock=True)
    values = body.model_dump(exclude_unset=True)
    row = trip_planning.create_item(db, a, user, values)
    return _item_out(db, a, user, row)


@router.patch("/trips/{trip_id}/plan/{item_id}", dependencies=[write_rate_limit])
def update_plan_item(
    trip_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PlanItemIn,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> PlanItemOut:
    a = _access(db, user, trip_id, lock=True)
    row = trip_planning.update_item(
        db, a, user, item_id, _if_match(if_match), body.model_dump(exclude_unset=True)
    )
    response.headers["ETag"] = f'"{row.version}"'
    return _item_out(db, a, user, row)


@router.delete("/trips/{trip_id}/plan/{item_id}", status_code=204, dependencies=[write_rate_limit])
def delete_plan_item(
    trip_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> None:
    a = _access(db, user, trip_id, lock=True)
    trip_planning.delete_item(db, a, user, item_id)


# --- The caller's private budget


class MyBudgetOut(BaseModel):
    amount: MoneyOut | None


class MyBudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null or "" removes it.
    amount: StrictStr | None = None


@router.get("/trips/{trip_id}/my-budget")
def get_my_budget(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> MyBudgetOut:
    a = _access(db, user, trip_id)
    row = trip_planning.personal_budget(db, a, user)
    return MyBudgetOut(amount=_money(db, row.amount_minor if row else None, a.trip.base_currency))


@router.put("/trips/{trip_id}/my-budget", dependencies=[write_rate_limit])
def set_my_budget(
    trip_id: uuid.UUID,
    body: MyBudgetIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> MyBudgetOut:
    a = _access(db, user, trip_id)
    row = trip_planning.set_personal_budget(db, a, user, body.amount)
    return MyBudgetOut(amount=_money(db, row.amount_minor if row else None, a.trip.base_currency))


# --- Analytics


class KeyAmount(BaseModel):
    key: str
    amount: MoneyOut


class DayAmount(BaseModel):
    day: date
    amount: MoneyOut


class MemberCost(BaseModel):
    user_id: uuid.UUID
    display_name: str
    paid: MoneyOut
    share: MoneyOut


class TripAnalyticsOut(BaseModel):
    currency: str
    # Plans (estimates): never spending.
    planned: MoneyOut
    planned_by_kind: list[KeyAmount]
    booked: MoneyOut
    deposits: MoneyOut
    # Approved expenses: the trip's real spending.
    actual: MoneyOut
    actual_by_kind: list[KeyAmount]
    by_category: list[KeyAmount]
    daily: list[DayAmount]
    member_count: int
    cost_per_member: MoneyOut
    members: list[MemberCost]
    shared_budget: MoneyOut | None
    shared_remaining: MoneyOut | None
    shared_percent: str | None
    # Only the caller's own figures: their share (spending) and cash they paid (not spending).
    my_share: MoneyOut
    my_paid: MoneyOut
    my_by_category: list[KeyAmount]
    my_budget: MoneyOut | None
    my_remaining: MoneyOut | None
    my_percent: str | None
    pending_expenses: int
    pending_settlements: int
    confirmed_settlements: MoneyOut
    outstanding: MoneyOut


def _percent(part: int, whole: int | None) -> str | None:
    if not whole:
        return None
    value = (part * 1000 + whole // 2) // whole
    return f"{value // 10}.{value % 10}"


@router.get("/trips/{trip_id}/analytics")
def trip_analytics(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> TripAnalyticsOut:
    a = _access(db, user, trip_id)
    x = trip_planning.analytics(db, a, user)
    c = x.currency
    names = {m.user_id: m.display_name for m in trips.members(db, a.trip.id)}

    def pairs(values: dict[str, int]) -> list[KeyAmount]:
        return [KeyAmount(key=k, amount=_m(db, v, c)) for k, v in values.items()]

    return TripAnalyticsOut(
        currency=c,
        planned=_m(db, x.planned_minor, c),
        planned_by_kind=pairs(x.planned_by_kind),
        booked=_m(db, x.booked_minor, c),
        deposits=_m(db, x.deposits_minor, c),
        actual=_m(db, x.actual_minor, c),
        actual_by_kind=pairs(x.actual_by_kind),
        by_category=pairs(x.by_category),
        daily=[DayAmount(day=d, amount=_m(db, v, c)) for d, v in x.daily.items()],
        member_count=x.member_count,
        cost_per_member=_m(db, x.actual_minor // x.member_count, c),
        members=[
            MemberCost(
                user_id=u,
                display_name=names.get(u, ""),
                paid=_m(db, paid, c),
                share=_m(db, share, c),
            )
            for u, (paid, share) in x.per_member.items()
        ],
        shared_budget=_money(db, x.shared_budget_minor, c),
        shared_remaining=(
            _m(db, x.shared_budget_minor - x.actual_minor, c)
            if x.shared_budget_minor is not None
            else None
        ),
        shared_percent=_percent(x.actual_minor, x.shared_budget_minor),
        my_share=_m(db, x.my_share_minor, c),
        my_paid=_m(db, x.my_paid_minor, c),
        my_by_category=pairs(x.my_by_category),
        my_budget=_money(db, x.personal_budget_minor, c),
        my_remaining=(
            _m(db, x.personal_budget_minor - x.my_share_minor, c)
            if x.personal_budget_minor is not None
            else None
        ),
        my_percent=_percent(x.my_share_minor, x.personal_budget_minor),
        pending_expenses=x.pending_expenses,
        pending_settlements=x.pending_settlements,
        confirmed_settlements=_m(db, x.confirmed_settlements_minor, c),
        outstanding=_m(db, x.outstanding_minor, c),
    )
