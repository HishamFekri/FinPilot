"""Trip planning, budgets and analytics (FINANCIAL_RULES.md §11.12).

- **Plan items** (bookings, journeys, activities, notes, links) are shared with the trip's members.
  Their estimates and deposits are never spending: only approved trip expenses are (§12). An item
  can point at the expense that recorded its real cost, which then counts once it's approved.
- **The shared budget** is a planned total for the group, in the base currency, compared with the
  approved expenses.
- **A personal budget** belongs to one member and is only ever read or written by that member
  (every query filters on the caller). It's compared with the member's allocated share of approved
  expenses (§11.10), never with cash paid, so nothing is counted twice.
- **Analytics** read the same approved expenses and shares as balances and reports.
"""

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    BOOKING_STATUSES,
    PLAN_ITEM_KINDS,
    TRANSPORT_MODES,
    TripExpense,
    TripPersonalBudget,
    TripPlanItem,
    User,
)
from app.services import money, trip_expenses
from app.services.text_safety import unsafe
from app.services.trips import Access, event, members, require_active

MAX_ITEMS = 200
_URL = re.compile(r"^https://[^\s]{1,490}$")
# Which plan kind a trip category stands for, when an expense isn't linked to a plan item.
CATEGORY_KIND = {"housing": "accommodation", "transportation": "transport", "travel": "transport"}


def _text(value: object, name: str, limit: int, *, required: bool = False) -> str | None:
    if value is None or not str(value).strip():
        if required:
            raise ApiError(422, "validation_error", f"{name} is required", {"field": name})
        return None
    text = " ".join(str(value).split()) if name != "notes" else str(value).strip()
    if unsafe(text, allow_newline=True) or len(text) > limit:
        raise ApiError(422, "validation_error", f"{name} isn't valid", {"field": name})
    return text


def _amount(db: Session, value: object, currency: str) -> int | None:
    if value is None or not str(value).strip():
        return None
    minor, _ = money.parse(db, str(value), currency)
    if minor < 0:
        raise ApiError(422, "amount_not_positive", "Amounts can't be negative")
    return minor


def _writable(a: Access) -> None:
    require_active(a)
    if a.trip.status not in ("active", "completed"):
        raise ApiError(409, "trip_read_only", "This trip is closed for changes")


def items(db: Session, a: Access) -> list[TripPlanItem]:
    return list(
        db.scalars(
            select(TripPlanItem)
            .where(TripPlanItem.trip_id == a.trip.id)
            .order_by(TripPlanItem.starts_on.nulls_last(), TripPlanItem.created_at)
        )
    )


def _item(db: Session, a: Access, item_id: uuid.UUID, *, lock: bool = False) -> TripPlanItem:
    query = select(TripPlanItem).where(
        TripPlanItem.id == item_id, TripPlanItem.trip_id == a.trip.id
    )
    found = db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise ApiError(404, "not_found", "Not found")
    return found


def _apply(db: Session, a: Access, row: TripPlanItem, values: dict[str, object]) -> None:
    code = a.trip.base_currency
    if "kind" in values:
        if values["kind"] not in PLAN_ITEM_KINDS:
            raise ApiError(422, "validation_error", "Unknown kind", {"field": "kind"})
        row.kind = str(values["kind"])
    if "title" in values:
        row.title = _text(values["title"], "title", 100, required=True) or row.title
    if "transport_mode" in values:
        mode = values["transport_mode"]
        if mode is not None and (mode not in TRANSPORT_MODES or row.kind != "transport"):
            raise ApiError(
                422, "validation_error", "Unknown transport", {"field": "transport_mode"}
            )
        row.transport_mode = str(mode) if mode else None
    if row.kind != "transport":
        row.transport_mode = None
    for name in ("starts_on", "ends_on"):
        if name in values:
            setattr(row, name, values[name])
    if row.starts_on and row.ends_on and row.ends_on < row.starts_on:
        raise ApiError(422, "dates_out_of_order", "The end date is before the start date")
    if "booking_status" in values:
        if values["booking_status"] not in BOOKING_STATUSES:
            raise ApiError(422, "validation_error", "Unknown status", {"field": "booking_status"})
        row.booking_status = str(values["booking_status"])
    if "estimated" in values:
        row.estimated_minor = _amount(db, values["estimated"], code)
    if "deposit" in values:
        row.deposit_minor = _amount(db, values["deposit"], code)
    if "reference" in values:
        row.reference = _text(values["reference"], "reference", 60)
    if "url" in values:
        url = _text(values["url"], "url", 500)
        if url is not None and not _URL.fullmatch(url):
            raise ApiError(422, "url_not_https", "Links must start with https://")
        row.url = url
    if "notes" in values:
        row.notes = _text(values["notes"], "notes", 1000)
    if "expense_id" in values:
        expense_id = values["expense_id"]
        if expense_id is not None:
            expense = db.get(TripExpense, expense_id)
            if expense is None or expense.trip_id != a.trip.id:
                raise ApiError(404, "not_found", "No such expense on this trip")
        row.expense_id = expense_id  # type: ignore[assignment]


def create_item(db: Session, a: Access, user: User, values: dict[str, object]) -> TripPlanItem:
    _writable(a)
    if len(items(db, a)) >= MAX_ITEMS:
        raise ApiError(409, "too_many_items", "This trip has the most plan items allowed")
    row = TripPlanItem(trip_id=a.trip.id, created_by=user.id, kind="note", title="-")
    _apply(db, a, row, {"kind": values.get("kind", "note"), **values})
    db.add(row)
    db.flush()
    event(db, a.trip.id, user.id, "trip.plan.added", entity_type="trip_plan_item", entity_id=row.id)
    return row


def _may_edit(a: Access, row: TripPlanItem, user: User) -> None:
    if row.created_by != user.id and not a.admin:
        raise ApiError(403, "forbidden", "Only who added it, or an admin, can change it")


def update_item(
    db: Session, a: Access, user: User, item_id: uuid.UUID, version: int, values: dict[str, object]
) -> TripPlanItem:
    _writable(a)
    row = _item(db, a, item_id, lock=True)
    _may_edit(a, row, user)
    if row.version != version:
        raise ApiError(412, "version_conflict", "This item changed; reload it")
    _apply(db, a, row, values)
    row.version += 1
    db.flush()
    event(db, a.trip.id, user.id, "trip.plan.updated", entity_type="trip_plan_item",
          entity_id=row.id)  # fmt: skip
    return row


def delete_item(db: Session, a: Access, user: User, item_id: uuid.UUID) -> None:
    """Plans and estimates aren't money: removing one never touches an expense."""
    _writable(a)
    row = _item(db, a, item_id, lock=True)
    _may_edit(a, row, user)
    db.delete(row)
    db.flush()
    event(db, a.trip.id, user.id, "trip.plan.removed", entity_type="trip_plan_item",
          entity_id=item_id)  # fmt: skip


# --- Personal budget: only the caller's own row, ever.


def personal_budget(db: Session, a: Access, user: User) -> TripPersonalBudget | None:
    return db.get(TripPersonalBudget, (a.trip.id, user.id))


def set_personal_budget(
    db: Session, a: Access, user: User, amount: str | None
) -> TripPersonalBudget | None:
    require_active(a)
    row = personal_budget(db, a, user)
    minor = _amount(db, amount, a.trip.base_currency)
    if not minor:
        if row is not None:
            db.delete(row)
            db.flush()
        return None
    if row is None:
        row = TripPersonalBudget(trip_id=a.trip.id, user_id=user.id, amount_minor=minor)
        db.add(row)
    else:
        row.amount_minor = minor
    db.flush()
    return row


# --- Analytics


@dataclass
class Analytics:
    currency: str
    planned_minor: int
    planned_by_kind: dict[str, int]
    booked_minor: int
    deposits_minor: int
    actual_minor: int
    actual_by_kind: dict[str, int]
    by_category: dict[str, int]
    daily: dict[date, int]
    member_count: int
    shared_budget_minor: int | None
    my_share_minor: int
    my_paid_minor: int
    my_by_category: dict[str, int]
    personal_budget_minor: int | None
    pending_expenses: int
    pending_settlements: int
    confirmed_settlements_minor: int
    outstanding_minor: int  # what the settlement suggestions would move in total
    per_member: dict[uuid.UUID, tuple[int, int]] = field(default_factory=dict)  # paid, share


def analytics(db: Session, a: Access, user: User) -> Analytics:
    trip = a.trip
    plan = items(db, a)
    live = [i for i in plan if i.booking_status != "cancelled"]
    planned_by_kind: dict[str, int] = defaultdict(int)
    for i in live:
        if i.estimated_minor:
            planned_by_kind[i.kind] += i.estimated_minor
    linked_kind = {i.expense_id: i.kind for i in plan if i.expense_id is not None}

    effective = trip_expenses.effective(db, trip.id)
    actual = 0
    by_category: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    daily: dict[date, int] = defaultdict(int)
    my_share = 0
    my_categories: dict[str, int] = defaultdict(int)
    for e in effective:
        amount = e.version.base_amount_minor
        actual += amount
        by_category[e.version.category_key] += amount
        kind = linked_kind.get(e.expense.id) or CATEGORY_KIND.get(e.version.category_key, "other")
        by_kind[kind if kind in ("accommodation", "transport") else "other"] += amount
        daily[e.version.expense_date] += amount
        for share in e.shares:
            if share.user_id == user.id:
                my_share += share.base_share_minor
                my_categories[e.version.category_key] += share.base_share_minor

    balances = trip_expenses.balances(db, trip)
    mine = balances.get(user.id)
    confirmed = sum(b.sent for b in balances.values())
    board = trip_expenses.dashboard(db, trip)
    budget = personal_budget(db, a, user)
    return Analytics(
        currency=trip.base_currency,
        planned_minor=sum(planned_by_kind.values()),
        planned_by_kind=dict(planned_by_kind),
        booked_minor=sum(i.estimated_minor or 0 for i in live if i.booking_status == "booked"),
        deposits_minor=sum(i.deposit_minor or 0 for i in live),
        actual_minor=actual,
        actual_by_kind=dict(by_kind),
        by_category=dict(sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))),
        daily=dict(sorted(daily.items())),
        member_count=sum(1 for m in members(db, trip.id) if m.status == "active") or 1,
        shared_budget_minor=trip.shared_budget_minor,
        my_share_minor=my_share,
        my_paid_minor=mine.paid if mine else 0,
        my_by_category=dict(sorted(my_categories.items(), key=lambda kv: (-kv[1], kv[0]))),
        personal_budget_minor=budget.amount_minor if budget else None,
        pending_expenses=board.pending_count,
        pending_settlements=trip_expenses.pending_settlements(db, trip.id),
        confirmed_settlements_minor=confirmed,
        outstanding_minor=sum(n for _, _, n in board.suggestions),
        per_member={u: (b.paid, b.owed) for u, b in balances.items()},
    )
