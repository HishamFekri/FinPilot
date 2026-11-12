"""Savings goals (PRD §3.10, FINANCIAL_RULES.md §4.12).

A goal is a target the person tracks **by hand**. Its progress is the sum of the amounts they
added minus the amounts they took off, kept as an append-only history (a database trigger refuses
UPDATE and DELETE). It is **never money in an account**: it isn't a balance, it isn't counted in
any total, and nothing moves between accounts. Goals aren't linked to accounts, so the same money
can't be counted twice by a link; the screen says progress is tracked, not held.

Progress changes only while a goal is active; a paused or archived goal keeps its figures. A goal
can't go below zero. Percentages are Decimal, one decimal, half up, and may exceed 100.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import SavingsGoal, SavingsGoalEvent, User
from app.services import money
from app.services.accounts import clean_text
from app.services.sessions import audit

NOTE_MAX = 200
MAX_GOALS = 50


@dataclass
class GoalView:
    goal: SavingsGoal
    progress: int
    events: list[SavingsGoalEvent] = field(default_factory=list)
    # A goal can follow an installment plan ("pay the car off early"). Its progress is then the
    # principal repaid ahead of schedule, read from the plan: never entered, never added twice.
    plan_id: uuid.UUID | None = None
    plan_name: str | None = None

    @property
    def remaining(self) -> int:
        return max(self.goal.target_minor - self.progress, 0)

    @property
    def percent(self) -> Decimal:
        return (Decimal(self.progress) * 100 / Decimal(self.goal.target_minor)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )

    @property
    def reached(self) -> bool:
        return self.progress >= self.goal.target_minor


def progress_of(db: Session, goal_id: uuid.UUID) -> int:
    """added - reduced, from the history (computed in Python: exact and obvious)."""
    total = 0
    for kind, amount in db.execute(
        select(SavingsGoalEvent.kind, SavingsGoalEvent.amount_minor).where(
            SavingsGoalEvent.goal_id == goal_id,
            SavingsGoalEvent.kind.in_(("added", "reduced")),
        )
    ):
        total += (amount or 0) if kind == "added" else -(amount or 0)
    return total


def owned(db: Session, user: User, goal_id: uuid.UUID, *, lock: bool = False) -> SavingsGoal:
    query = select(SavingsGoal).where(SavingsGoal.id == goal_id, SavingsGoal.user_id == user.id)
    found = db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise ApiError(404, "not_found", "Goal not found")
    return found


def history(db: Session, goal: SavingsGoal) -> list[SavingsGoalEvent]:
    return list(
        db.scalars(
            select(SavingsGoalEvent)
            .where(SavingsGoalEvent.goal_id == goal.id)
            .order_by(SavingsGoalEvent.created_at.desc(), SavingsGoalEvent.id)
        )
    )


def _linked(db: Session, goal: SavingsGoal) -> GoalView | None:
    from app.services import installments  # plans build on goals, not the other way round

    plan = installments.for_goal(db, goal.id)
    if plan is None:
        return None
    user = db.get(User, goal.user_id)
    assert user is not None  # noqa: S101 - guaranteed by the foreign key
    ahead = installments.view(db, user, plan).ahead_minor
    return GoalView(goal, ahead, plan_id=plan.id, plan_name=plan.name)


def view(db: Session, goal: SavingsGoal, *, with_history: bool = False) -> GoalView:
    linked = _linked(db, goal)
    if linked is not None:
        linked.events = history(db, goal) if with_history else []
        return linked
    return GoalView(goal, progress_of(db, goal.id), history(db, goal) if with_history else [])


def list_for(db: Session, user: User, *, include_archived: bool = False) -> list[GoalView]:
    query = select(SavingsGoal).where(SavingsGoal.user_id == user.id)
    if not include_archived:
        query = query.where(SavingsGoal.status != "archived")
    goals = list(db.scalars(query.order_by(SavingsGoal.status, SavingsGoal.created_at)))
    return [view(db, g) for g in goals]


def _amount(db: Session, amount: str, currency: str) -> int:
    minor, _ = money.parse(db, amount, currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    return minor


def _note(value: str | None) -> str | None:
    return clean_text(value, max_length=NOTE_MAX) if value and value.strip() else None


def _event(db: Session, goal: SavingsGoal, kind: str, **values: object) -> SavingsGoalEvent:
    # Its own clock time (not the transaction's), so events from one request keep their order.
    row = SavingsGoalEvent(
        goal_id=goal.id, user_id=goal.user_id, kind=kind, created_at=datetime.now(UTC), **values
    )
    db.add(row)
    db.flush()
    return row


def create(
    db: Session,
    user: User,
    *,
    name: str,
    target: str,
    currency: str,
    target_date: date | None,
    starting: str | None = None,
) -> SavingsGoal:
    count = int(db.scalar(select(func.count()).where(SavingsGoal.user_id == user.id)) or 0)
    if count >= MAX_GOALS:
        raise ApiError(409, "too_many_goals", "You have the most goals allowed")
    target_minor = _amount(db, target, currency)
    goal = SavingsGoal(
        user_id=user.id,
        name=clean_text(name, max_length=60),
        target_minor=target_minor,
        currency=currency,
        target_date=target_date,
    )
    db.add(goal)
    db.flush()
    _event(db, goal, "created", target_minor=target_minor)
    if starting and starting.strip():
        _event(db, goal, "added", amount_minor=_amount(db, starting, currency))
    audit(db, "goal.created", user.id, entity_type="savings_goal", entity_id=goal.id)
    return goal


def _check_version(goal: SavingsGoal, version: int | None) -> None:
    if version is not None and goal.version != version:
        raise ApiError(412, "version_conflict", "This goal changed; reload and try again")


def move(
    db: Session,
    user: User,
    goal_id: uuid.UUID,
    direction: str,
    amount: str,
    note: str | None,
) -> SavingsGoal:
    """Adds to or takes off the tracked progress. Moves no money."""
    goal = owned(db, user, goal_id, lock=True)
    if goal.status != "active":
        raise ApiError(409, "goal_not_active", "Resume the goal to change its progress")
    if _linked(db, goal) is not None:
        raise ApiError(
            409,
            "goal_follows_plan",
            "This goal follows an installment plan: its progress comes from your payments",
        )
    minor = _amount(db, amount, goal.currency)
    if direction == "reduce" and minor > progress_of(db, goal.id):
        raise ApiError(409, "goal_progress_negative", "That's more than the goal's progress")
    _event(db, goal, "added" if direction == "add" else "reduced", amount_minor=minor,
           note=_note(note))  # fmt: skip
    goal.version += 1
    db.flush()
    audit(db, f"goal.{direction}", user.id, entity_type="savings_goal", entity_id=goal.id)
    return goal


def update(
    db: Session,
    user: User,
    goal_id: uuid.UUID,
    version: int,
    *,
    name: str | None = None,
    target: str | None = None,
    target_date: date | None = None,
    clear_target_date: bool = False,
    status: str | None = None,
) -> SavingsGoal:
    goal = owned(db, user, goal_id, lock=True)
    _check_version(goal, version)
    if name is not None and name.strip() and name.strip() != goal.name:
        goal.name = clean_text(name, max_length=60)
        _event(db, goal, "renamed")
    if target is not None:
        minor = _amount(db, target, goal.currency)
        if minor != goal.target_minor:
            goal.target_minor = minor
            _event(db, goal, "target_changed", target_minor=minor)
    if clear_target_date:
        goal.target_date = None
    elif target_date is not None:
        goal.target_date = target_date
    if status is not None and status != goal.status:
        if status not in ("active", "paused", "archived"):
            raise ApiError(422, "validation_error", "Choose active, paused or archived")
        goal.status = status
        _event(db, goal, "status_changed", status=status)
    goal.version += 1
    db.flush()
    audit(db, "goal.updated", user.id, entity_type="savings_goal", entity_id=goal.id)
    return goal
