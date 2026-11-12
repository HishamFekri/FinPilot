"""Personal links, possible duplicates, and the person's own trip position (FINANCIAL_RULES §11).

Owner-only. Every row is looked up together with the session's user (404 otherwise); nothing here
is ever part of a trip response. Changes need CSRF; posting ones also need an Idempotency-Key.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_active_user, require_writable_user
from app.api.v1.accounts import MoneyOut, _no_store, write_rate_limit
from app.api.v1.finance import TransactionOut, _transactions_out, gateway
from app.api.v1.idempotent import run_once
from app.api.v1.trips import ExpenseIn
from app.api.v1.trips import _detail as expense_detail
from app.core.errors import ApiError
from app.db.models import (
    PersonalLink,
    Settlement,
    Transaction,
    Trip,
    TripExpense,
    TripExpenseVersion,
    TripMember,
    User,
)
from app.db.session import get_db
from app.services import money, trip_expenses, trip_links, trips

router = APIRouter(tags=["links"], dependencies=[Depends(_no_store)])


def _money(db: Session, minor: int, currency: str) -> MoneyOut:
    exponent = money.currency(db, currency, for_new_records=False).exponent
    return MoneyOut(amount=money.to_amount(minor, exponent), currency=currency)


class ReviewOut(BaseModel):
    needed: bool
    reason: Literal["expense_changed", "expense_cancelled", "settlement_reversed"] | None
    current_base_amount: MoneyOut | None


class LinkOut(BaseModel):
    id: uuid.UUID
    trip_id: uuid.UUID
    trip_name: str
    kind: Literal["expense_payment", "settlement_paid", "settlement_received"]
    expense_id: uuid.UUID | None
    settlement_id: uuid.UUID | None
    status: Literal["active", "unlinked"]
    # Signed, trip base currency: + paid into the trip, - received back.
    linked_base_amount: MoneyOut
    transaction: TransactionOut
    review: ReviewOut
    created_at: datetime
    resolved_at: datetime | None


def _links_out(db: Session, rows: list[PersonalLink]) -> list[LinkOut]:
    if not rows:
        return []
    names = dict(
        db.execute(select(Trip.id, Trip.name).where(Trip.id.in_({r.trip_id for r in rows}))).all()
    )
    documents = list(
        db.scalars(select(Transaction).where(Transaction.id.in_([r.transaction_id for r in rows])))
    )
    shown = {t.id: t for t in _transactions_out(db, documents)}
    out = []
    for r in rows:
        state = trip_links.review(db, r)
        out.append(
            LinkOut(
                id=r.id,
                trip_id=r.trip_id,
                trip_name=names[r.trip_id],
                kind=r.kind,  # type: ignore[arg-type]
                expense_id=r.expense_id,
                settlement_id=r.settlement_id,
                status=r.status,  # type: ignore[arg-type]
                linked_base_amount=_money(db, r.linked_base_amount_minor, r.base_currency),
                transaction=shown[r.transaction_id],
                review=ReviewOut(
                    needed=state.needed,
                    reason=state.reason,  # type: ignore[arg-type]
                    current_base_amount=_money(db, state.current_base_amount, r.base_currency)
                    if state.current_base_amount is not None
                    else None,
                ),
                created_at=r.created_at,
                resolved_at=r.resolved_at,
            )
        )
    return out


def _one(db: Session, link: PersonalLink) -> dict[str, Any]:
    return _links_out(db, [link])[0].model_dump(mode="json")


@router.get("/links")
def list_links(
    trip_id: uuid.UUID | None = None,
    include_unlinked: bool = False,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[LinkOut]:
    return _links_out(db, trip_links.links(db, user, trip_id, active_only=not include_unlinked))


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_id: uuid.UUID
    expense_id: uuid.UUID | None = None
    settlement_id: uuid.UUID | None = None
    mode: Literal["create", "attach"] = "create"
    # create: the account and the amount actually charged or received, in its currency.
    account_id: uuid.UUID | None = None
    amount: Annotated[str | None, Field(max_length=32)] = None
    effective_date: date | None = None
    description: Annotated[str | None, Field(max_length=200)] = None
    # attach: the hand-entered transaction, its version, and the person's explicit confirmation.
    transaction_id: uuid.UUID | None = None
    transaction_version: int | None = None
    confirm: bool = False


@router.post("/links", status_code=201, dependencies=[write_rate_limit])
def create_link(
    request: Request,
    body: LinkIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    """Records a trip expense the person paid, or a confirmed payment they made or received, in
    one of their own accounts. Never someone else's; never automatically."""

    def operation() -> tuple[int, Any]:
        if body.mode == "attach":
            if body.transaction_id is None or body.transaction_version is None:
                raise ApiError(422, "validation_error", "Choose the transaction to attach")
            if not body.confirm:
                raise ApiError(422, "confirmation_required", "Confirm the change first")
            link = trip_links.attach(
                db, user, body.trip_id, expense_id=body.expense_id,
                settlement_id=body.settlement_id, transaction_id=body.transaction_id,
                version=body.transaction_version,
            )  # fmt: skip
        else:
            if body.account_id is None or body.amount is None:
                raise ApiError(422, "validation_error", "Choose an account and an amount")
            link = trip_links.create(
                db, user, body.trip_id, expense_id=body.expense_id,
                settlement_id=body.settlement_id, account_id=body.account_id,
                amount=body.amount, effective_date=body.effective_date,
                description=body.description.strip() if body.description else None,
            )  # fmt: skip
        return 201, _one(db, link)

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


# --- One step: record a trip expense (or approve it) and record it in my account


class AccountChoice(BaseModel):
    """Where the payer's own cash came from. Only the payer can give it; never shown to others."""

    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    amount: Annotated[str, Field(max_length=32)]
    effective_date: date | None = None


class ExpenseWithLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_id: uuid.UUID
    expense: ExpenseIn
    link: AccountChoice


class ApproveWithLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_id: uuid.UUID
    version_id: uuid.UUID
    link: AccountChoice


class ExpenseLinkedOut(BaseModel):
    expense: dict[str, Any]  # the trip's own view of the expense (no personal data)
    link: LinkOut


def _link_it(
    db: Session, user: User, trip_id: uuid.UUID, expense_id: uuid.UUID, choice: AccountChoice
) -> PersonalLink:
    return trip_links.create(
        db, user, trip_id, expense_id=expense_id, settlement_id=None,
        account_id=choice.account_id, amount=choice.amount, effective_date=choice.effective_date,
    )  # fmt: skip


@router.post("/links/expense", status_code=201, dependencies=[write_rate_limit])
def create_expense_and_link(
    request: Request,
    body: ExpenseWithLinkIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    """The payer records a trip expense and its cash in their own account, in one transaction
    (§11.11). Only for an expense they paid themselves, which is approved at once; if anything
    about the link is wrong, nothing is saved and the client can offer to save without it."""

    def operation() -> tuple[int, Any]:
        if body.expense.payer_user_id != user.id:
            raise ApiError(403, "not_payer", "Only the person who paid can record their account")
        a = trips.access(db, user, body.trip_id, lock=True)
        expense, version, _ = trip_expenses.create(
            db, gateway(request), user, a, payer_id=user.id, data=body.expense.to_input(),
            replaces=body.expense.replaces_expense_id,
        )  # fmt: skip
        if version.state != "approved":  # pragma: no cover - the payer's own expense
            raise ApiError(409, "expense_not_approved", "Only an approved expense can be recorded")
        link = _link_it(db, user, a.trip.id, expense.id, body.link)
        out = ExpenseLinkedOut(
            expense=expense_detail(db, a, user, expense.id), link=_links_out(db, [link])[0]
        )
        return 201, out.model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.post("/links/approve", dependencies=[write_rate_limit])
def approve_and_link(
    request: Request,
    body: ApproveWithLinkIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    """Someone else recorded an expense the caller paid: the caller approves it and records it in
    their own account at once. Their account is touched only by their own approval."""

    def operation() -> tuple[int, Any]:
        a = trips.access(db, user, body.trip_id, lock=True)
        version = db.get(TripExpenseVersion, body.version_id)
        if version is None or version.trip_id != a.trip.id:
            raise ApiError(404, "not_found", "Expense not found")
        expense = db.get(TripExpense, version.expense_id)
        if expense is None or expense.payer_user_id != user.id:
            raise ApiError(403, "not_payer", "Only the person who paid can record their account")
        live = db.scalar(
            select(PersonalLink.id).where(
                PersonalLink.owner_user_id == user.id,
                PersonalLink.expense_id == expense.id,
                PersonalLink.status == "active",
            )
        )
        if live is not None:
            raise ApiError(
                409, "already_linked", "This is already recorded: approve it, then review it"
            )
        trip_expenses.decide(db, user, a, body.version_id, "approve", None)
        link = _link_it(db, user, a.trip.id, expense.id, body.link)
        out = ExpenseLinkedOut(
            expense=expense_detail(db, a, user, expense.id), link=_links_out(db, [link])[0]
        )
        return 200, out.model_dump(mode="json")

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


class LinkableOut(BaseModel):
    """Something the person could record in an account: they paid it or are a party to it."""

    trip_id: uuid.UUID
    kind: Literal["expense_payment", "settlement_paid", "settlement_received"]
    expense_id: uuid.UUID | None
    settlement_id: uuid.UUID | None
    description: str | None
    date: date
    # What the trip says moved: the receipt (or payment) and its base value. Prefill only.
    amount: MoneyOut
    base_amount: MoneyOut


@router.get("/links/suggestions")
def linkable(
    trip_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[LinkableOut]:
    """Items in a trip the person may link and hasn't: never a share, a reversal or a pending
    item. Nothing is ever matched or linked on their behalf."""
    if db.get(TripMember, (trip_id, user.id)) is None:
        raise ApiError(404, "not_found", "Trip not found")
    live = {(r.expense_id or r.settlement_id) for r in trip_links.links(db, user, trip_id)}
    out: list[LinkableOut] = []
    for version in db.scalars(
        select(TripExpenseVersion)
        .join(TripExpense, TripExpense.id == TripExpenseVersion.expense_id)
        .where(
            TripExpense.trip_id == trip_id,
            TripExpense.payer_user_id == user.id,
            TripExpenseVersion.state == "approved",
            TripExpenseVersion.kind != "cancellation",
        )
        .order_by(TripExpenseVersion.expense_date.desc())
    ):
        if version.expense_id in live:
            continue
        out.append(
            LinkableOut(
                trip_id=trip_id,
                kind="expense_payment",
                expense_id=version.expense_id,
                settlement_id=None,
                description=version.description,
                date=version.expense_date,
                amount=_money(db, version.amount_minor, version.currency),
                base_amount=_money(db, version.base_amount_minor, version.base_currency),
            )
        )
    for row in db.scalars(
        select(Settlement)
        .where(
            Settlement.trip_id == trip_id,
            Settlement.kind == "payment",
            Settlement.state == "confirmed",
            (Settlement.payer_user_id == user.id) | (Settlement.recipient_user_id == user.id),
        )
        .order_by(Settlement.paid_on.desc())
    ):
        if row.id in live or trip_links.reversal_of(db, row.id) is not None:
            continue
        out.append(
            LinkableOut(
                trip_id=trip_id,
                kind="settlement_paid" if row.payer_user_id == user.id else "settlement_received",
                expense_id=None,
                settlement_id=row.id,
                description=row.note,
                date=row.paid_on,
                amount=_money(db, row.amount_minor, row.currency),
                base_amount=_money(db, row.base_amount_minor, row.base_currency),
            )
        )
    return out


@router.get("/links/{link_id}")
def get_link(
    link_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> LinkOut:
    return _links_out(db, [trip_links.owned(db, user, link_id)])[0]


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["update", "keep", "reverse"]
    amount: Annotated[str | None, Field(max_length=32)] = None
    effective_date: date | None = None


@router.post("/links/{link_id}/resolve", dependencies=[write_rate_limit])
def resolve_link(
    link_id: uuid.UUID,
    request: Request,
    body: ResolveIn,
    idempotency_key: Annotated[str | None, Header()] = None,
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> Any:
    def operation() -> tuple[int, Any]:
        link = trip_links.resolve(
            db, user, link_id, body.action, amount=body.amount, effective_date=body.effective_date
        )
        return 200, _one(db, link)

    return run_once(db, user, request, idempotency_key, body.model_dump(mode="json"), operation)


@router.delete("/links/{link_id}", dependencies=[write_rate_limit])
def delete_link(
    link_id: uuid.UUID,
    personal_transaction: Literal["reverse", "convert"],
    user: User = Depends(require_writable_user),
    db: Session = Depends(get_db),
) -> LinkOut:
    """Unlinks: reverses the personal transaction, or converts it back into a plain expense or
    income. Repeating it changes nothing."""
    link = trip_links.unlink(db, user, link_id, personal_transaction)
    return _links_out(db, [link])[0]


# --- Possible duplicates


class CandidateOut(BaseModel):
    transaction: TransactionOut
    strength: Literal["strong", "possible"]
    match: Literal["original", "base", "share", "near_base"]


class DuplicateOut(BaseModel):
    trip_id: uuid.UUID
    trip_name: str
    expense_id: uuid.UUID
    description: str
    expense_date: date
    amount: MoneyOut
    base_amount: MoneyOut
    my_share: MoneyOut
    paid_by_me: bool
    candidates: list[CandidateOut]


@router.get("/me/possible-duplicates")
def possible_duplicates(
    trip_id: uuid.UUID | None = None,
    expense_id: uuid.UUID | None = None,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
) -> list[DuplicateOut]:
    groups = trip_links.duplicates(db, user, trip_id, expense_id)
    shown = {
        t.id: t
        for t in _transactions_out(db, [c.transaction for g in groups for c in g.candidates])
    }
    return [
        DuplicateOut(
            trip_id=g.trip.id,
            trip_name=g.trip.name,
            expense_id=g.expense.id,
            description=g.version.description,
            expense_date=g.version.expense_date,
            amount=_money(db, g.version.amount_minor, g.version.currency),
            base_amount=_money(db, g.version.base_amount_minor, g.version.base_currency),
            my_share=_money(db, g.my_share, g.version.base_currency),
            paid_by_me=g.paid_by_me,
            candidates=[
                CandidateOut(
                    transaction=shown[c.transaction.id],
                    strength=c.strength,  # type: ignore[arg-type]
                    match=c.match,  # type: ignore[arg-type]
                )
                for c in g.candidates
            ],
        )
        for g in groups
    ]


class DismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: uuid.UUID
    expense_id: uuid.UUID


@router.post("/me/possible-duplicates/dismiss", status_code=204, dependencies=[write_rate_limit])
def dismiss_duplicate(
    body: DismissIn, user: User = Depends(require_writable_user), db: Session = Depends(get_db)
) -> None:
    trip_links.dismiss(db, user, body.transaction_id, body.expense_id)


# --- My position in a trip (§11.4)


class ShareOut(BaseModel):
    expense_id: uuid.UUID
    description: str
    expense_date: date
    category_key: str
    amount: MoneyOut


class PositionOut(BaseModel):
    trip_id: uuid.UUID
    currency: str
    paid: MoneyOut  # "Amount you paid": trip expenses you paid
    share: MoneyOut  # "Your share": what the trip cost you; counts as your spending
    net: MoneyOut  # + others owe you, - you owe
    owed_to_me: MoneyOut
    i_owe: MoneyOut
    settled_sent: MoneyOut
    settled_received: MoneyOut
    pending_sent: MoneyOut
    pending_received: MoneyOut
    cash_position: MoneyOut  # paid + sent - received = share + net
    linked: MoneyOut  # recorded in your accounts (signed sum of live links)
    unrecorded: MoneyOut  # cash_position - linked; informational only
    links: int
    needs_review: int
    shares: list[ShareOut]


@router.get("/me/trips/{trip_id}/position")
def my_position(
    trip_id: uuid.UUID, user: User = Depends(require_active_user), db: Session = Depends(get_db)
) -> PositionOut:
    p = trip_links.position(db, user, trip_id)
    code = p.trip.base_currency

    def m(minor: int) -> MoneyOut:
        return _money(db, minor, code)

    return PositionOut(
        trip_id=p.trip.id,
        currency=code,
        paid=m(p.paid),
        share=m(p.share),
        net=m(p.net),
        owed_to_me=m(max(p.net, 0)),
        i_owe=m(max(-p.net, 0)),
        settled_sent=m(p.sent),
        settled_received=m(p.received),
        pending_sent=m(p.pending_sent),
        pending_received=m(p.pending_received),
        cash_position=m(p.cash_position),
        linked=m(p.linked),
        unrecorded=m(p.unrecorded),
        links=p.links,
        needs_review=p.needs_review,
        shares=[
            ShareOut(
                expense_id=s.expense_id,
                description=s.description,
                expense_date=s.expense_date,
                category_key=s.category_key,
                amount=m(s.amount),
            )
            for s in sorted(p.shares, key=lambda s: s.expense_date, reverse=True)
        ],
    )
