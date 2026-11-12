"""Trips, membership, invitations, and shared expenses (API_SPEC.md §9, §10).

Everything needs a session; changes need CSRF; creating an expense, proposing a change, and
confirming or rejecting one need an Idempotency-Key; editing a trip needs If-Match. Someone who
was never a member gets 404. Members are shown as id, display name, role and status only: never
an email (SECURITY.md §4.5). Invitation tokens travel in the request body, never the URL.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy.orm import Session

from app.api.deps import require_active_user, require_writable_user
from app.api.v1.accounts import (
    CurrencyCode,
    MoneyIn,
    MoneyOut,
    _if_match,
    _no_store,
    write_rate_limit,
)
from app.api.v1.finance import gateway
from app.api.v1.idempotent import run_once
from app.core.errors import ApiError
from app.db.models import (
    FxRate,
    Trip,
    TripDispute,
    TripExpenseVersion,
    TripInvitation,
    User,
)
from app.db.session import get_db
from app.services import allocation, money, settlements, trip_disputes, trip_expenses, trips

router = APIRouter(tags=["trips"], dependencies=[Depends(_no_store)])


# --- Shapes


class Person(BaseModel):
    user_id: uuid.UUID
    display_name: str


class MemberOut(Person):
    role: str
    status: str


class TripIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr
    base_currency: CurrencyCode
    description: StrictStr | None = None
    destination: StrictStr | None = None
    start_date: date | None = None
    end_date: date | None = None


class TripPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr | None = None
    base_currency: CurrencyCode | None = None
    description: StrictStr | None = None
    destination: StrictStr | None = None
    start_date: date | None = None
    end_date: date | None = None
    # The group's planned total in the base currency; null or "" removes it (admins only).
    shared_budget: StrictStr | None = None


class Abilities(BaseModel):
    edit: bool
    admin: bool
    owner: bool
    add_expense: bool


class TripOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    destination: str | None
    start_date: date | None
    end_date: date | None
    base_currency: str
    status: str
    # upcoming, ongoing, ended (past its dates, not closed), completed, cancelled, archived.
    phase: str
    cancelled_at: datetime | None
    shared_budget: MoneyOut | None
    version: int
    role: str
    member_status: str
    base_currency_locked: bool
    can: Abilities


class TripListItem(BaseModel):
    id: uuid.UUID
    name: str
    destination: str | None
    start_date: date | None
    end_date: date | None
    base_currency: str
    status: str
    phase: str
    role: str
    member_status: str
    members: int
    waiting_on_me: int


def _today(db: Session, user_id: uuid.UUID) -> date:
    user = db.get(User, user_id)
    assert user is not None  # noqa: S101 - guaranteed by the foreign key
    return trips.today_for(user)


def _trip_out(db: Session, a: trips.Access) -> TripOut:
    t, m = a.trip, a.member
    budget = None
    if t.shared_budget_minor is not None:
        exponent = money.currency(db, t.base_currency, for_new_records=False).exponent
        budget = MoneyOut(
            amount=money.to_amount(t.shared_budget_minor, exponent), currency=t.base_currency
        )
    return TripOut(
        id=t.id,
        name=t.name,
        description=t.description,
        destination=t.destination,
        start_date=t.start_date,
        end_date=t.end_date,
        base_currency=t.base_currency,
        status=t.status,
        phase=trips.phase(t, _today(db, m.user_id)),
        cancelled_at=t.cancelled_at,
        shared_budget=budget,
        version=t.version,
        role=m.role,
        member_status=m.status,
        base_currency_locked=trips.has_expenses(db, t.id),
        can=Abilities(
            edit=a.admin,
            admin=a.admin,
            owner=a.owner,
            add_expense=a.active and t.status == "active",
        ),
    )


def _names(db: Session, trip_id: uuid.UUID) -> dict[uuid.UUID, trips.MemberView]:
    return {m.user_id: m for m in trips.members(db, trip_id)}


def _person(names: dict[uuid.UUID, trips.MemberView], user_id: uuid.UUID) -> Person:
    found = names.get(user_id)
    return Person(user_id=user_id, display_name=found.display_name if found else "")


def _with_etag(response: Response, out: TripOut) -> TripOut:
    response.headers["ETag"] = f'"{out.version}"'
    return out


# --- Trips


@router.get("/trips")
def list_trips(
    status: Literal["active", "completed", "cancelled", "archived"] | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[TripListItem]:
    rows = trips.list_for(db, user, status)
    waiting = trip_expenses.waiting_counts(db, user.id)
    for trip_id, n in settlements.waiting_counts(db, user.id).items():
        waiting[trip_id] = waiting.get(trip_id, 0) + n
    out = []
    today = trips.today_for(user)
    for t, m in rows:
        members = trips.members(db, t.id)
        out.append(
            TripListItem(
                id=t.id,
                name=t.name,
                destination=t.destination,
                start_date=t.start_date,
                end_date=t.end_date,
                base_currency=t.base_currency,
                status=t.status,
                phase=trips.phase(t, today),
                role=m.role,
                member_status=m.status,
                members=sum(1 for x in members if x.status == "active"),
                waiting_on_me=waiting.get(t.id, 0),
            )
        )
    return out


@router.post("/trips", status_code=201, dependencies=[write_rate_limit])
def create_trip(
    request: Request,
    body: TripIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        trip = trips.create(
            db,
            user,
            name=body.name,
            base_currency=body.base_currency,
            description=body.description,
            destination=body.destination,
            start_date=body.start_date,
            end_date=body.end_date,
        )
        return 201, _trip_out(db, trips.access(db, user, trip.id)).model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.get("/trips/{trip_id}")
def get_trip(
    trip_id: uuid.UUID,
    response: Response,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> TripOut:
    return _with_etag(response, _trip_out(db, trips.access(db, user, trip_id)))


@router.patch("/trips/{trip_id}", dependencies=[write_rate_limit])
def update_trip(
    trip_id: uuid.UUID,
    body: TripPatch,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> TripOut:
    a = trips.access(db, user, trip_id, lock=True)
    trips.require_admin(a)  # who may edit is answered before what was sent
    trips.update(db, a, user, _if_match(if_match), body.model_dump(exclude_unset=True))
    return _with_etag(response, _trip_out(db, a))


class ArchiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_outstanding: bool = False


def _status_route(action: str) -> None:
    def change_trip_status(
        trip_id: uuid.UUID,
        body: ArchiveIn | None = None,
        user: User = Depends(require_writable_user),
        db: Session = Depends(get_db),
    ) -> TripOut:
        a = trips.access(db, user, trip_id, lock=True)
        trips.set_status(
            db, a, user, action, acknowledge=bool(body and body.acknowledge_outstanding)
        )
        return _trip_out(db, a)

    change_trip_status.__name__ = f"{action}_trip"
    router.post(f"/trips/{{trip_id}}/{action}", dependencies=[write_rate_limit])(change_trip_status)


for _action in ("complete", "reopen", "cancel", "reinstate", "archive", "unarchive"):
    _status_route(_action)


# --- Members


@router.get("/trips/{trip_id}/members")
def list_members(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[MemberOut]:
    a = trips.access(db, user, trip_id)
    views = trips.members(db, trip_id)
    if not a.active:
        # A former member sees only the people they share records with (SECURITY.md §4.4).
        related: set[uuid.UUID] = {user.id}
        for v in trip_expenses.list_views(db, a):
            related |= {v.expense.payer_user_id, v.expense.created_by}
            related |= {s.user_id for rows in v.shares.values() for s in rows}
        views = [m for m in views if m.user_id in related]
    return [MemberOut(**m.__dict__) for m in views]


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "member"]


@router.patch("/trips/{trip_id}/members/{member_id}", dependencies=[write_rate_limit])
def change_role(
    trip_id: uuid.UUID,
    member_id: uuid.UUID,
    body: RoleIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    trips.change_role(db, trips.access(db, user, trip_id, lock=True), user, member_id, body.role)
    return [MemberOut(**m.__dict__) for m in trips.members(db, trip_id)]


class UserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID


@router.post("/trips/{trip_id}/transfer-ownership", dependencies=[write_rate_limit])
def transfer_ownership(
    trip_id: uuid.UUID,
    body: UserIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    trips.transfer_ownership(db, trips.access(db, user, trip_id, lock=True), user, body.user_id)
    return [MemberOut(**m.__dict__) for m in trips.members(db, trip_id)]


@router.post("/trips/{trip_id}/leave", status_code=204, dependencies=[write_rate_limit])
def leave_trip(
    trip_id: uuid.UUID, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> Response:
    trips.leave(db, trips.access(db, user, trip_id, lock=True), user)
    return Response(status_code=204)


@router.delete(
    "/trips/{trip_id}/members/{member_id}", status_code=204, dependencies=[write_rate_limit]
)
def remove_member(
    trip_id: uuid.UUID,
    member_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Response:
    trips.remove(db, trips.access(db, user, trip_id, lock=True), user, member_id)
    return Response(status_code=204)


# --- Invitations


class InvitationOut(BaseModel):
    id: uuid.UUID
    label: str | None
    status: str
    expired: bool
    expires_at: datetime
    created_at: datetime


class InvitationCreated(InvitationOut):
    # Shown once. The web app turns it into https://<host>/<locale>/invite#<token>.
    token: str


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: StrictStr | None = None


def _invitation_out(row: TripInvitation) -> InvitationOut:
    return InvitationOut(
        id=row.id,
        label=row.invitee_label,
        status=row.status,
        expired=row.status == "pending" and row.expires_at <= datetime.now(UTC),
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


@router.get("/trips/{trip_id}/invitations")
def list_invitations(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[InvitationOut]:
    return [_invitation_out(r) for r in trips.invitations(db, trips.access(db, user, trip_id))]


@router.post("/trips/{trip_id}/invitations", status_code=201, dependencies=[write_rate_limit])
def create_invitation(
    trip_id: uuid.UUID,
    body: InviteIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> InvitationCreated:
    row, token = trips.invite(db, trips.access(db, user, trip_id), user, body.label)
    db.refresh(row)
    return InvitationCreated(**_invitation_out(row).model_dump(), token=token)


@router.delete(
    "/trips/{trip_id}/invitations/{invitation_id}", status_code=204, dependencies=[write_rate_limit]
)
def revoke_invitation(
    trip_id: uuid.UUID,
    invitation_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Response:
    trips.revoke(db, trips.access(db, user, trip_id), user, invitation_id)
    return Response(status_code=204)


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[StrictStr, Field(min_length=10, max_length=200)]


class InvitePreviewOut(BaseModel):
    trip_name: str
    destination: str | None
    start_date: date | None
    end_date: date | None
    base_currency: str
    inviter: str
    already_member: bool


@router.post("/invitations/preview", dependencies=[write_rate_limit])
def preview_invitation(
    body: TokenIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> InvitePreviewOut:
    found = trips.preview(db, user, body.token)
    return InvitePreviewOut(
        trip_name=found.trip.name,
        destination=found.trip.destination,
        start_date=found.trip.start_date,
        end_date=found.trip.end_date,
        base_currency=found.trip.base_currency,
        inviter=found.inviter,
        already_member=found.already_member,
    )


@router.post("/invitations/accept", dependencies=[write_rate_limit])
def accept_invitation(
    body: TokenIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> TripOut:
    trip = trips.accept(db, user, body.token)
    return _trip_out(db, trips.access(db, user, trip.id))


@router.post("/invitations/decline", status_code=204, dependencies=[write_rate_limit])
def decline_invitation(
    body: TokenIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> Response:
    trips.decline(db, user, body.token)
    return Response(status_code=204)


# --- Activity


class ActivityOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor: Person | None
    action: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    details: dict[str, Any]


@router.get("/trips/{trip_id}/activity")
def trip_activity(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[ActivityOut]:
    a = trips.access(db, user, trip_id)
    trips.require_active(a)  # former members don't see the feed (SECURITY.md §4.4)
    names = _names(db, trip_id)
    return [
        ActivityOut(
            id=e.id,
            occurred_at=e.occurred_at,
            actor=_person(names, e.actor_user_id) if e.actor_user_id else None,
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            details=e.event_metadata,
        )
        for e in trips.activity(db, trip_id)
    ]


# --- Expenses


class RateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_id: uuid.UUID | None = None
    manual: Annotated[StrictStr, Field(max_length=40)] | None = None


class ShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    value: Annotated[StrictStr, Field(max_length=40)] | None = None


class SplitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["equal", "fixed", "percentage"]
    shares: Annotated[list[ShareIn], Field(min_length=1, max_length=50)]


class ExpenseContentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: StrictStr
    category_key: StrictStr
    expense_date: date
    amount: MoneyIn
    split: SplitIn
    rate: RateIn | None = None
    acknowledge_stale_rate: bool = False
    notes: StrictStr | None = None

    def to_input(self) -> trip_expenses.ExpenseInput:
        return trip_expenses.ExpenseInput(
            description=self.description,
            category_key=self.category_key,
            expense_date=self.expense_date,
            amount=self.amount.amount,
            currency=self.amount.currency,
            split_method=self.split.method,
            shares=[allocation.ShareInput(s.user_id, s.value) for s in self.split.shares],
            notes=self.notes,
            rate=trip_expenses.RateChoice(
                rate_id=self.rate.rate_id if self.rate else None,
                manual_rate=self.rate.manual if self.rate else None,
                accept_stale=self.acknowledge_stale_rate,
            ),
        )


class ExpenseIn(ExpenseContentIn):
    payer_user_id: uuid.UUID
    replaces_expense_id: uuid.UUID | None = None


class RateOut(BaseModel):
    rate: str
    source: str
    provider: str | None
    upstream: str | None
    rate_date: date
    is_manual: bool


class ShareOut(Person):
    input_value: str | None
    share: MoneyOut
    base_share: MoneyOut


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    kind: str
    state: str
    proposed_by: Person
    description: str
    notes: str | None
    category_key: str
    expense_date: date
    amount: MoneyOut
    base_amount: MoneyOut
    rate: RateOut | None
    split_method: str
    shares: list[ShareOut]
    decided_by: Person | None
    decided_at: datetime | None
    decision_reason: str | None
    created_at: datetime


class ExpenseAbilities(BaseModel):
    correct: bool
    cancel: bool
    approve: bool
    withdraw: bool
    dispute: bool


class DisputeAbilities(BaseModel):
    withdraw: bool
    resolve: bool


class DisputeOut(BaseModel):
    id: uuid.UUID
    expense_id: uuid.UUID
    version_id: uuid.UUID
    raised_by: Person
    reason: str
    status: str
    resolution_note: str | None
    resolved_by: Person | None
    created_at: datetime
    decided_at: datetime | None
    can: DisputeAbilities


class DisputeSummary(BaseModel):
    open_count: int
    mine: uuid.UUID | None  # the caller's open dispute on the current version


class ExpenseOut(BaseModel):
    id: uuid.UUID
    status: str
    is_corrected: bool
    has_pending_correction: bool
    classification: str | None
    payer: Person
    created_by: Person
    replaces_expense_id: uuid.UUID | None
    shown: VersionOut  # the approved version, else the latest
    current_version: VersionOut | None
    pending_version: VersionOut | None
    history: list[VersionOut] | None  # detail only
    waiting_on_me: bool
    can: ExpenseAbilities
    disputes: DisputeSummary
    dispute_history: list[DisputeOut] | None = None  # detail only


class Formatter:
    """Minor units to money, and ids to names, for one response."""

    def __init__(self, db: Session, trip: Trip) -> None:
        self.db = db
        self.names = _names(db, trip.id)
        self._exponents: dict[str, int] = {}

    def money(self, minor: int, currency: str) -> MoneyOut:
        if currency not in self._exponents:
            self._exponents[currency] = money.currency(
                self.db, currency, for_new_records=False
            ).exponent
        return MoneyOut(amount=money.to_amount(minor, self._exponents[currency]), currency=currency)

    def person(self, user_id: uuid.UUID) -> Person:
        return _person(self.names, user_id)


def _rate_out(rate: FxRate | None) -> RateOut | None:
    if rate is None:
        return None
    return RateOut(
        rate=format(rate.rate.normalize(), "f"),
        source=rate.source,
        provider=rate.provider,
        upstream=rate.upstream,
        rate_date=rate.rate_date,
        is_manual=rate.source == "manual",
    )


def _version_out(
    f: Formatter, v: TripExpenseVersion, view: trip_expenses.ExpenseView
) -> VersionOut:
    shares = sorted(view.shares.get(v.id, []), key=lambda s: f.person(s.user_id).display_name)
    return VersionOut(
        id=v.id,
        version_no=v.version_no,
        kind=v.kind,
        state=v.state,
        proposed_by=f.person(v.proposed_by),
        description=v.description,
        notes=v.notes,
        category_key=v.category_key,
        expense_date=v.expense_date,
        amount=f.money(v.amount_minor, v.currency),
        base_amount=f.money(v.base_amount_minor, v.base_currency),
        rate=_rate_out(view.rates.get(v.rate_id) if v.rate_id else None),
        split_method=v.split_method,
        shares=[
            ShareOut(
                **f.person(s.user_id).model_dump(),
                input_value=format(s.input_value.normalize(), "f")
                if s.input_value is not None
                else None,
                share=f.money(s.share_minor, v.currency),
                base_share=f.money(s.base_share_minor, v.base_currency),
            )
            for s in shares
        ],
        decided_by=f.person(v.decided_by) if v.decided_by else None,
        decided_at=v.decided_at,
        decision_reason=v.decision_reason,
        created_at=v.created_at,
    )


def _expense_out(
    f: Formatter,
    a: trips.Access,
    user: User,
    view: trip_expenses.ExpenseView,
    *,
    detail: bool = False,
    open_disputes: list[TripDispute] | None = None,
    all_disputes: list[TripDispute] | None = None,
) -> ExpenseOut:
    current, pending = view.current, view.pending
    open_list = open_disputes or []
    mine = next((d for d in open_list if d.raised_by == user.id), None)
    status = view.status
    beneficiaries = {s.user_id for s in view.shares.get(current.id, [])} if current else set()
    payer = view.expense.payer_user_id
    can_propose = (
        a.active
        and a.trip.status == "active"
        and status != "cancelled"
        and pending is None
        and (a.admin or user.id in (view.expense.created_by, payer))
    )
    return ExpenseOut(
        id=view.expense.id,
        status=status,
        is_corrected=bool(current and current.version_no > 1),
        has_pending_correction=bool(current and pending),
        classification=trip_expenses.classification(payer, beneficiaries) if current else None,
        payer=f.person(payer),
        created_by=f.person(view.expense.created_by),
        replaces_expense_id=view.expense.replaces_expense_id,
        shown=_version_out(f, view.shown, view),
        current_version=_version_out(f, current, view) if current else None,
        pending_version=_version_out(f, pending, view) if pending else None,
        history=[_version_out(f, v, view) for v in view.history] if detail else None,
        waiting_on_me=bool(pending and payer == user.id),
        can=ExpenseAbilities(
            correct=can_propose,
            cancel=can_propose and current is not None,
            approve=bool(pending and payer == user.id),
            withdraw=bool(
                pending
                and a.active
                and (a.admin or pending.proposed_by == user.id)
                and payer != user.id
            ),
            dispute=bool(
                current
                and status == "approved"
                and a.trip.status != "archived"
                and user.id in beneficiaries
                and user.id != payer
                and mine is None
            ),
        ),
        disputes=DisputeSummary(open_count=len(open_list), mine=mine.id if mine else None),
        dispute_history=[_dispute_out(f, a, user, d) for d in all_disputes]
        if detail and all_disputes is not None
        else None,
    )


@router.get("/trips/{trip_id}/expenses")
def list_expenses(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> list[ExpenseOut]:
    a = trips.access(db, user, trip_id)
    f = Formatter(db, a.trip)
    disputes = trip_disputes.open_by_expense(db, a.trip.id)
    return [
        _expense_out(f, a, user, v, open_disputes=disputes.get(v.expense.id))
        for v in trip_expenses.list_views(db, a)
    ]


class PreviewShare(Person):
    share: MoneyOut
    base_share: MoneyOut


class PreviewOut(BaseModel):
    amount: MoneyOut
    base_amount: MoneyOut
    rate: str | None
    classification: str
    shares: list[PreviewShare]
    warnings: list[str]


class PreviewIn(ExpenseContentIn):
    payer_user_id: uuid.UUID


@router.post("/trips/{trip_id}/expenses/preview", dependencies=[write_rate_limit])
def preview_expense(
    request: Request,
    trip_id: uuid.UUID,
    body: PreviewIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> PreviewOut:
    a = trips.access(db, user, trip_id)
    c = trip_expenses.preview(db, gateway(request), user, a, body.to_input())
    f = Formatter(db, a.trip)
    return PreviewOut(
        amount=f.money(c.amount_minor, c.currency),
        base_amount=f.money(c.base_amount_minor, c.base_currency),
        rate=format(c.rate.normalize(), "f") if c.rate is not None else None,
        classification=trip_expenses.classification(
            body.payer_user_id, {s.user_id for s in c.shares}
        ),
        shares=[
            PreviewShare(
                **f.person(s.user_id).model_dump(),
                share=f.money(s.share_minor, c.currency),
                base_share=f.money(c.base_shares[s.user_id], c.base_currency),
            )
            for s in c.shares
        ],
        warnings=c.warnings,
    )


def _detail(db: Session, a: trips.Access, user: User, expense_id: uuid.UUID) -> dict[str, Any]:
    f = Formatter(db, a.trip)
    view = trip_expenses.view(db, a, expense_id)
    every = [d for d in trip_disputes.for_trip(db, a) if d.expense_id == expense_id]
    return _expense_out(
        f,
        a,
        user,
        view,
        detail=True,
        open_disputes=[d for d in every if d.status == "open"],
        all_disputes=every,
    ).model_dump(mode="json")


def _dispute_out(f: "Formatter", a: trips.Access, user: User, d: TripDispute) -> DisputeOut:
    return DisputeOut(
        id=d.id,
        expense_id=d.expense_id,
        version_id=d.version_id,
        raised_by=f.person(d.raised_by),
        reason=d.reason,
        status=d.status,
        resolution_note=d.resolution_note,
        resolved_by=f.person(d.resolved_by) if d.resolved_by else None,
        created_at=d.created_at,
        decided_at=d.decided_at,
        can=DisputeAbilities(
            withdraw=d.status == "open" and d.raised_by == user.id,
            resolve=d.status == "open"
            and a.admin
            and not trip_disputes.interested(f.db, d, user.id),
        ),
    )


@router.post("/trips/{trip_id}/expenses", status_code=201, dependencies=[write_rate_limit])
def create_expense(
    request: Request,
    trip_id: uuid.UUID,
    body: ExpenseIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        a = trips.access(db, user, trip_id, lock=True)
        expense, _, _ = trip_expenses.create(
            db,
            gateway(request),
            user,
            a,
            payer_id=body.payer_user_id,
            data=body.to_input(),
            replaces=body.replaces_expense_id,
        )
        return 201, _detail(db, a, user, expense.id)

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.get("/trips/{trip_id}/expenses/{expense_id}")
def get_expense(
    trip_id: uuid.UUID,
    expense_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> Any:
    return _detail(db, trips.access(db, user, trip_id), user, expense_id)


@router.post(
    "/trips/{trip_id}/expenses/{expense_id}/versions",
    status_code=201,
    dependencies=[write_rate_limit],
)
def correct_expense(
    request: Request,
    trip_id: uuid.UUID,
    expense_id: uuid.UUID,
    body: ExpenseContentIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        a = trips.access(db, user, trip_id, lock=True)
        trip_expenses.propose(db, gateway(request), user, a, expense_id, data=body.to_input())
        return 201, _detail(db, a, user, expense_id)

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.post(
    "/trips/{trip_id}/expenses/{expense_id}/cancel",
    status_code=201,
    dependencies=[write_rate_limit],
)
def cancel_expense(
    request: Request,
    trip_id: uuid.UUID,
    expense_id: uuid.UUID,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        a = trips.access(db, user, trip_id, lock=True)
        trip_expenses.propose(db, gateway(request), user, a, expense_id, data=None)
        return 201, _detail(db, a, user, expense_id)

    return run_once(db, user, request, idempotency_key, {"cancel": str(expense_id)}, operation)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[StrictStr, Field(max_length=500)] | None = None


@router.post(
    "/trips/{trip_id}/expense-versions/{version_id}/{action}", dependencies=[write_rate_limit]
)
def decide_version(
    request: Request,
    trip_id: uuid.UUID,
    version_id: uuid.UUID,
    action: Literal["approve", "reject", "withdraw"],
    body: DecisionIn | None = None,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        a = trips.access(db, user, trip_id, lock=True)
        version = trip_expenses.decide(
            db, user, a, version_id, action, body.reason if body else None
        )
        return 200, _detail(db, a, user, version.expense_id)

    payload = {"action": action, "reason": body.reason if body else None}
    return run_once(db, user, request, idempotency_key, payload, operation)


# --- Dashboard (PRD.md §4.6)


class CategoryTotal(BaseModel):
    key: str
    amount: MoneyOut


class MemberBalanceOut(MemberOut):
    paid: MoneyOut
    owed: MoneyOut
    sent: MoneyOut
    received: MoneyOut
    net: MoneyOut


class Suggestion(BaseModel):
    """A suggested payment: not paid until someone records it and the recipient confirms."""

    payer: Person
    recipient: Person
    amount: MoneyOut
    involves_disputed_expense: bool


class SettlementAbilities(BaseModel):
    confirm: bool
    reject: bool
    cancel: bool
    reverse: bool


class SettlementOut(BaseModel):
    id: uuid.UUID
    kind: str  # payment, reversal
    state: str  # pending, confirmed, rejected, cancelled
    status: str  # what to show: state, or reversal_pending / reversed for a payment
    payer: Person
    recipient: Person
    initiated_by: Person
    amount: MoneyOut  # what was actually paid
    base_amount: MoneyOut  # what it counts for in the trip's currency, fixed when recorded
    rate: RateOut | None = None  # paid in another currency: the rate used
    paid_on: date
    note: str | None
    reverses_settlement_id: uuid.UUID | None
    corrects_settlement_id: uuid.UUID | None
    reversal_id: uuid.UUID | None
    decided_by: Person | None
    decided_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    waiting_on_me: bool
    can: SettlementAbilities


class WaitingOut(BaseModel):
    expense_id: uuid.UUID
    version_id: uuid.UUID
    kind: str
    description: str
    amount: MoneyOut
    proposed_by: Person


class DashboardOut(BaseModel):
    base_currency: str
    total: MoneyOut
    shared: MoneyOut
    personal: MoneyOut
    categories: list[CategoryTotal]
    members: list[MemberBalanceOut]
    suggestions: list[Suggestion]
    pending_count: int
    waiting_on_me: list[WaitingOut]
    settlements_waiting: list[SettlementOut]  # payments this person must confirm or reject
    pending_settlements: int
    disputes_attention: list[DisputeOut]  # open disputes this person can resolve, or on their
    balanced: bool  # the nets sum to exactly zero (always true; shown as a check)


@router.get("/trips/{trip_id}/dashboard")
def trip_dashboard(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> DashboardOut:
    a = trips.access(db, user, trip_id)
    trip = a.trip
    f = Formatter(db, trip)
    d = trip_expenses.dashboard(db, trip)
    code = trip.base_currency
    members = [
        MemberBalanceOut(
            **m.__dict__,
            paid=f.money(d.balances[m.user_id].paid, code),
            owed=f.money(d.balances[m.user_id].owed, code),
            sent=f.money(d.balances[m.user_id].sent, code),
            received=f.money(d.balances[m.user_id].received, code),
            net=f.money(d.balances[m.user_id].net, code),
        )
        for m in trips.members(db, trip.id)
        if m.user_id in d.balances
    ]
    suggestions = d.suggestions
    disputed = _disputed_people(db, trip)
    every_view = {v.row.id: v for v in settlements.views(db, a)}
    if not a.active:  # a former member sees only their own balance and suggestions (§4.4)
        members = [m for m in members if m.user_id == user.id]
        suggestions = [s for s in suggestions if user.id in (s[0], s[1])]
    return DashboardOut(
        base_currency=code,
        total=f.money(d.total, code),
        shared=f.money(d.shared, code),
        personal=f.money(d.personal, code),
        categories=[CategoryTotal(key=k, amount=f.money(v, code)) for k, v in d.categories.items()],
        members=members,
        suggestions=[
            Suggestion(
                payer=f.person(p),
                recipient=f.person(r),
                amount=f.money(n, code),
                # The person paying should know their amount includes a disputed expense.
                involves_disputed_expense=p in disputed,
            )
            for p, r, n in suggestions
        ],
        pending_count=d.pending_count,
        waiting_on_me=[
            WaitingOut(
                expense_id=v.expense_id,
                version_id=v.id,
                kind=v.kind,
                description=v.description,
                amount=f.money(v.amount_minor, v.currency),
                proposed_by=f.person(v.proposed_by),
            )
            for v in trip_expenses.waiting_on(db, trip.id, user.id)
        ],
        settlements_waiting=[
            _settlement_out(f, a, user, every_view[r.id])
            for r in settlements.waiting_on(db, trip.id, user.id)
            if r.id in every_view
        ],
        pending_settlements=trip_expenses.pending_settlements(db, trip.id),
        disputes_attention=[
            _dispute_out(f, a, user, x)
            for x in trip_disputes.for_trip(db, a, status="open")
            # Those who can resolve it, and the payer for awareness; never the person who raised it.
            if _dispute_out(f, a, user, x).can.resolve
            or (trip_disputes.is_payer(db, x, user.id) and x.raised_by != user.id)
        ],
        balanced=sum(b.net for b in d.balances.values()) == 0,
    )


def _disputed_people(db: Session, trip: Trip) -> set[uuid.UUID]:
    """People whose balance includes an expense with an open dispute (payer or beneficiary)."""
    people: set[uuid.UUID] = set()
    open_disputes = trip_disputes.open_by_expense(db, trip.id)
    for item in trip_expenses.effective(db, trip.id):
        if item.expense.id in open_disputes:
            people.add(item.expense.payer_user_id)
            people |= {share.user_id for share in item.shares}
    return people


def _settlement_out(
    f: Formatter, a: trips.Access, user: User, v: settlements.SettlementView
) -> SettlementOut:
    r = v.row
    counterparty = r.recipient_user_id if r.initiated_by == r.payer_user_id else r.payer_user_id
    party = user.id in (r.payer_user_id, r.recipient_user_id)
    open_trip = a.trip.status != "archived"
    return SettlementOut(
        id=r.id,
        kind=r.kind,
        state=r.state,
        status=v.status,
        payer=f.person(r.payer_user_id),
        recipient=f.person(r.recipient_user_id),
        initiated_by=f.person(r.initiated_by),
        amount=f.money(r.amount_minor, r.currency),
        base_amount=f.money(r.base_amount_minor, r.base_currency),
        rate=_rate_out(f.db.get(FxRate, r.rate_id)) if r.rate_id else None,
        paid_on=r.paid_on,
        note=r.note,
        reverses_settlement_id=r.reverses_settlement_id,
        corrects_settlement_id=r.corrects_settlement_id,
        reversal_id=v.reversal.id if v.reversal else None,
        decided_by=f.person(r.decided_by) if r.decided_by else None,
        decided_at=r.decided_at,
        decision_reason=r.decision_reason,
        created_at=r.created_at,
        waiting_on_me=r.state == "pending" and user.id == counterparty,
        can=SettlementAbilities(
            confirm=open_trip and r.state == "pending" and user.id == counterparty,
            reject=open_trip and r.state == "pending" and user.id == counterparty,
            cancel=open_trip and r.state == "pending" and user.id == r.initiated_by,
            reverse=open_trip
            and party
            and r.kind == "payment"
            and r.state == "confirmed"
            and v.status == "confirmed",
        ),
    )


# --- Balances, suggestions and settlements (API_SPEC.md §11)


class BalancesOut(BaseModel):
    base_currency: str
    as_of: datetime
    members: list[MemberBalanceOut]
    pending_summary: dict[str, int]


@router.get("/trips/{trip_id}/balances")
def trip_balances(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> BalancesOut:
    a = trips.access(db, user, trip_id)
    f = Formatter(db, a.trip)
    code = a.trip.base_currency
    values = trip_expenses.balances(db, a.trip)
    rows = [
        MemberBalanceOut(
            **m.__dict__,
            paid=f.money(values[m.user_id].paid, code),
            owed=f.money(values[m.user_id].owed, code),
            sent=f.money(values[m.user_id].sent, code),
            received=f.money(values[m.user_id].received, code),
            net=f.money(values[m.user_id].net, code),
        )
        for m in trips.members(db, a.trip.id)
        if m.user_id in values
    ]
    if not a.active:
        rows = [r for r in rows if r.user_id == user.id]
    return BalancesOut(
        base_currency=code,
        as_of=datetime.now(UTC),
        members=rows,
        pending_summary={
            "expenses": trips.pending_versions(db, a.trip.id),
            "settlements": trip_expenses.pending_settlements(db, a.trip.id),
        },
    )


class SuggestionsOut(BaseModel):
    items: list[Suggestion]
    based_on: Literal["confirmed_only"] = "confirmed_only"
    note_code: Literal["suggestions_not_guaranteed_minimal"] = "suggestions_not_guaranteed_minimal"
    pending_settlements_between_listed_members: list[SettlementOut]


@router.get("/trips/{trip_id}/settlements/suggestions")
def settlement_suggestions(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> SuggestionsOut:
    a = trips.access(db, user, trip_id)
    f = Formatter(db, a.trip)
    code = a.trip.base_currency
    items = allocation.suggest(trip_expenses.nets(db, a.trip))
    if not a.active:
        items = [s for s in items if user.id in (s[0], s[1])]
    disputed = _disputed_people(db, a.trip)
    listed = {p for s in items for p in (s[0], s[1])}
    pending = [
        _settlement_out(f, a, user, v)
        for v in settlements.views(db, a)
        if v.row.state == "pending"
        and v.row.payer_user_id in listed
        and v.row.recipient_user_id in listed
    ]
    return SuggestionsOut(
        items=[
            Suggestion(
                payer=f.person(p),
                recipient=f.person(r),
                amount=f.money(n, code),
                # The person paying should know their amount includes a disputed expense.
                involves_disputed_expense=p in disputed,
            )
            for p, r, n in items
        ],
        pending_settlements_between_listed_members=pending,
    )


@router.get("/trips/{trip_id}/settlements")
def list_settlements(
    trip_id: uuid.UUID,
    state: Literal["pending", "confirmed", "rejected", "cancelled"] | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[SettlementOut]:
    a = trips.access(db, user, trip_id)
    f = Formatter(db, a.trip)
    return [
        _settlement_out(f, a, user, v)
        for v in settlements.views(db, a)
        if state is None or v.row.state == state
    ]


class SettlementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_user_id: uuid.UUID
    amount: MoneyIn
    paid_on: date | None = None
    note: Annotated[StrictStr, Field(max_length=200)] | None = None
    corrects_settlement_id: uuid.UUID | None = None
    confirm_duplicate: bool = False
    # Paid in another currency: a stored rate, a typed rate, or the trip-currency amount settled.
    rate: RateIn | None = None
    base_amount: Annotated[StrictStr, Field(max_length=30)] | None = None
    acknowledge_stale_rate: bool = False


class SettlementCreated(SettlementOut):
    warnings: list[str]


def _settlement_payload(
    db: Session, a: trips.Access, user: User, settlement_id: uuid.UUID
) -> SettlementOut:
    f = Formatter(db, a.trip)
    found = next((v for v in settlements.views(db, a) if v.row.id == settlement_id), None)
    if found is None:
        raise ApiError(404, "not_found", "Payment not found")
    return _settlement_out(f, a, user, found)


@router.post("/trips/{trip_id}/settlements", status_code=201, dependencies=[write_rate_limit])
def record_settlement(
    request: Request,
    trip_id: uuid.UUID,
    body: SettlementIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        a = trips.access(db, user, trip_id, lock=True)
        row, warnings = settlements.record(
            db,
            user,
            a,
            recipient_id=body.recipient_user_id,
            amount=body.amount.amount,
            currency=body.amount.currency,
            paid_on=body.paid_on,
            note=body.note,
            corrects=body.corrects_settlement_id,
            confirm_duplicate=body.confirm_duplicate,
            rate=settlements.PaymentRate(
                rate_id=body.rate.rate_id if body.rate else None,
                manual_rate=body.rate.manual if body.rate else None,
                base_amount=body.base_amount,
                accept_stale=body.acknowledge_stale_rate,
            ),
            gateway=gateway(request),
        )
        out = _settlement_payload(db, a, user, row.id)
        return 201, SettlementCreated(**out.model_dump(), warnings=warnings).model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.get("/trips/{trip_id}/settlements/{settlement_id}")
def get_settlement(
    trip_id: uuid.UUID,
    settlement_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> SettlementOut:
    return _settlement_payload(db, trips.access(db, user, trip_id), user, settlement_id)


class SettlementDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[StrictStr, Field(max_length=500)] | None = None


def _settlement_action(action: str) -> None:
    def decide_settlement(
        request: Request,
        trip_id: uuid.UUID,
        settlement_id: uuid.UUID,
        body: SettlementDecisionIn | None = None,
        idempotency_key: Annotated[str | None, Header()] = None,
        user: User = Depends(require_writable_user),
        db: Session = Depends(get_db),
    ) -> Any:
        def operation() -> tuple[int, Any]:
            a = trips.access(db, user, trip_id, lock=True)
            if action == "reverse":
                row = settlements.reverse(db, user, a, settlement_id)
                return 201, _settlement_payload(db, a, user, row.id).model_dump(mode="json")
            row = settlements.decide(
                db, user, a, settlement_id, action, body.reason if body else None
            )
            return 200, _settlement_payload(db, a, user, row.id).model_dump(mode="json")

        payload = {"action": action, "reason": body.reason if body else None}
        return run_once(db, user, request, idempotency_key, payload, operation)

    decide_settlement.__name__ = f"{action}_settlement"
    router.post(
        f"/trips/{{trip_id}}/settlements/{{settlement_id}}/{action}",
        dependencies=[write_rate_limit],
    )(decide_settlement)


for _settle_action in ("confirm", "reject", "cancel", "reverse"):
    _settlement_action(_settle_action)


# --- Disputes (FINANCIAL_RULES.md §12.8)


@router.get("/trips/{trip_id}/disputes")
def list_disputes(
    trip_id: uuid.UUID,
    status: Literal["open", "withdrawn", "upheld", "dismissed", "closed_superseded"] | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[DisputeOut]:
    a = trips.access(db, user, trip_id)
    f = Formatter(db, a.trip)
    return [_dispute_out(f, a, user, d) for d in trip_disputes.for_trip(db, a, status=status)]


class DisputeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: uuid.UUID
    reason: Annotated[StrictStr, Field(min_length=1, max_length=500)]


@router.post(
    "/trips/{trip_id}/expenses/{expense_id}/disputes",
    status_code=201,
    dependencies=[write_rate_limit],
)
def raise_dispute(
    request: Request,
    trip_id: uuid.UUID,
    expense_id: uuid.UUID,
    body: DisputeIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        a = trips.access(db, user, trip_id, lock=True)
        trip_disputes.raise_dispute(db, user, a, expense_id, body.version_id, body.reason)
        return 201, _detail(db, a, user, expense_id)

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.post("/trips/{trip_id}/disputes/{dispute_id}/withdraw", dependencies=[write_rate_limit])
def withdraw_dispute(
    trip_id: uuid.UUID,
    dispute_id: uuid.UUID,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> DisputeOut:
    a = trips.access(db, user, trip_id, lock=True)
    row = trip_disputes.withdraw(db, user, a, dispute_id)
    return _dispute_out(Formatter(db, a.trip), a, user, row)


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["upheld", "dismissed"]
    note: Annotated[StrictStr, Field(min_length=1, max_length=500)]


@router.post("/trips/{trip_id}/disputes/{dispute_id}/resolve", dependencies=[write_rate_limit])
def resolve_dispute(
    trip_id: uuid.UUID,
    dispute_id: uuid.UUID,
    body: ResolveIn,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> DisputeOut:
    a = trips.access(db, user, trip_id, lock=True)
    row = trip_disputes.resolve(db, user, a, dispute_id, body.outcome, body.note)
    return _dispute_out(Formatter(db, a.trip), a, user, row)
