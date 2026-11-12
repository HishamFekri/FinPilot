"""Recurring finances: expected money, confirmed one occurrence at a time (FINANCIAL_RULES.md §5a).

- **R1** A rule and its occurrences never post. Only confirming does, through the same services as a
  manual entry (`transactions.create`, `debts.pay`), with `created_via = 'recurring'`.
- **R2** Confirmation locks the occurrence row, requires it still pending, posts, and links the
  transaction in one database transaction. `transaction_id` is unique and `confirmed` requires it,
  so even two racing requests can't post twice; the Idempotency-Key replays a retry.
- **R3** Occurrences are generated lazily when read, up to 35 days ahead, with
  `INSERT … ON CONFLICT DO NOTHING` on `(rule_id, window_start)`: idempotent, no worker needed.
- **R4** Each occurrence snapshots the expected amount, currency and account. The actual amount,
  date and account belong to the transaction, so expected and actual are both kept.
- **R7** Editing applies forward: only pending occurrences whose window hasn't started are replaced.
- **R8** Pausing generates nothing; resuming starts after today, so missed windows aren't invented.
- **Void**: if the posted transaction is later voided, its occurrence becomes pending again
  (done by `transactions.void`).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    Category,
    Debt,
    FinancialAccount,
    RecurringOccurrence,
    RecurringRule,
    Transaction,
    User,
)
from app.services import conversion, debts, fx, money, schedule, transactions
from app.services.accounts import today_for
from app.services.categories import owned as owned_category
from app.services.sessions import audit

HORIZON_DAYS = 35
KINDS = ("income", "expense", "debt_payment")
FREQUENCIES = ("weekly", "biweekly", "monthly", "yearly")


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found")


def plan_of(rule: RecurringRule) -> schedule.Plan:
    return schedule.Plan(
        frequency=rule.frequency,
        start_date=rule.start_date,
        window_start_day=rule.window_start_day,
        window_end_day=rule.window_end_day,
        month=rule.month,
        end_date=rule.end_date,
        total=rule.total_occurrences,
    )


# --- Rules


@dataclass
class RuleDraft:
    kind: str
    name: str
    notes: str | None
    amount: str | None  # None: variable, no fixed expectation
    currency: str
    account_id: uuid.UUID
    category_id: uuid.UUID | None
    debt_id: uuid.UUID | None
    frequency: str
    window_start_day: int | None
    window_end_day: int | None
    month: int | None
    start_date: date | None
    end_date: date | None
    total_occurrences: int | None
    remind_days_before: int
    reminders: bool
    # Shown in the subscriptions view: a monthly or yearly expense (same engine, same rules).
    is_subscription: bool = False


def _account(db: Session, user: User, account_id: uuid.UUID) -> FinancialAccount:
    found = db.scalar(
        select(FinancialAccount).where(
            FinancialAccount.id == account_id, FinancialAccount.user_id == user.id
        )
    )
    if found is None:
        raise not_found()
    if found.status != "active":
        raise ApiError(409, "account_archived", "Choose an active account")
    return found


def _check_schedule(draft: RuleDraft) -> None:
    if draft.frequency not in FREQUENCIES:
        raise ApiError(422, "validation_error", "Unknown frequency")
    monthly = draft.frequency in ("monthly", "yearly")
    if monthly:
        first, last = draft.window_start_day, draft.window_end_day or draft.window_start_day
        if first is None or last is None or not (1 <= first <= last <= 31):
            raise ApiError(422, "invalid_window", "Choose days from 1 to 31, first to last")
        draft.window_end_day = last
    else:
        draft.window_start_day = draft.window_end_day = None
    if draft.frequency == "yearly":
        if draft.month is None or not 1 <= draft.month <= 12:
            raise ApiError(422, "invalid_window", "Choose a month")
    else:
        draft.month = None
    if draft.end_date is not None and draft.start_date and draft.end_date < draft.start_date:
        raise ApiError(422, "invalid_window", "The end date is before the start date")
    if draft.total_occurrences is not None and not 1 <= draft.total_occurrences <= 600:
        raise ApiError(422, "invalid_window", "Choose 1 to 600 payments")
    if not 0 <= draft.remind_days_before <= 14:
        raise ApiError(422, "validation_error", "Reminders start 0 to 14 days before")
    if draft.is_subscription and (
        draft.kind != "expense" or draft.frequency not in ("monthly", "yearly")
    ):
        raise ApiError(422, "invalid_subscription", "A subscription is a monthly or yearly expense")


def _check_links(db: Session, user: User, draft: RuleDraft) -> tuple[Category | None, Debt | None]:
    if draft.kind not in KINDS:
        raise ApiError(422, "validation_error", "Unknown kind")
    money.currency(db, draft.currency)
    _account(db, user, draft.account_id)
    category = debt = None
    if draft.kind in ("income", "expense"):
        if draft.category_id is None:
            raise ApiError(422, "validation_error", "Choose a category")
        category = owned_category(db, user, draft.category_id)
        if category.kind != draft.kind:
            raise ApiError(422, "category_kind_mismatch", "Choose a category of the same kind")
        if category.status != "active":
            raise ApiError(409, "category_archived", "An archived category can't be used")
    else:
        if draft.debt_id is None:
            raise ApiError(422, "validation_error", "Choose a debt")
        debt = debts.owned(db, user, draft.debt_id)
        if draft.currency != debt.currency:
            raise ApiError(422, "currency_mismatch", "Installments are in the debt's currency")
    return category, debt


def _amount(db: Session, draft: RuleDraft) -> int | None:
    if draft.amount is None:
        return None
    minor, _ = money.parse(db, draft.amount, draft.currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    return minor


def create_rule(db: Session, user: User, draft: RuleDraft) -> RecurringRule:
    today = today_for(user)
    draft.start_date = draft.start_date or today
    _check_schedule(draft)
    category, _ = _check_links(db, user, draft)
    rule = RecurringRule(
        user_id=user.id,
        kind=draft.kind,
        name=draft.name,
        notes=draft.notes,
        amount_minor=_amount(db, draft),
        currency=draft.currency,
        account_id=draft.account_id,
        category_id=category.id if category else None,
        category_kind=category.kind if category else None,
        debt_id=draft.debt_id if draft.kind == "debt_payment" else None,
        frequency=draft.frequency,
        window_start_day=draft.window_start_day,
        window_end_day=draft.window_end_day,
        month=draft.month,
        start_date=draft.start_date,
        end_date=draft.end_date,
        total_occurrences=draft.total_occurrences,
        # A new rule starts from today: past windows aren't invented as overdue.
        generate_from=max(draft.start_date, today),
        remind_days_before=draft.remind_days_before,
        reminders=draft.reminders,
        is_subscription=draft.is_subscription,
    )
    db.add(rule)
    db.flush()
    generate(db, rule, today)
    audit(db, "recurring.rule_created", user.id, entity_type="recurring_rule", entity_id=rule.id)
    return rule


def owned_rule(db: Session, user: User, rule_id: uuid.UUID, *, lock: bool = False) -> RecurringRule:
    query = select(RecurringRule).where(
        RecurringRule.id == rule_id, RecurringRule.user_id == user.id
    )
    if lock:
        query = query.with_for_update()
    found = db.scalar(query)
    if found is None:
        raise not_found()
    return found


def _check_version(rule: RecurringRule, version: int) -> None:
    if rule.version != version:
        raise ApiError(
            412, "version_conflict", "The rule changed; reload it", {"version": rule.version}
        )


def _drop_future(db: Session, rule: RecurringRule, today: date) -> None:
    """Pending occurrences whose window hasn't started: replaced or dropped. History stays."""
    db.execute(
        delete(RecurringOccurrence).where(
            RecurringOccurrence.rule_id == rule.id,
            RecurringOccurrence.status == "pending",
            RecurringOccurrence.window_start > today,
        )
    )


EDITABLE = (
    "name", "notes", "amount", "currency", "account_id", "category_id", "window_start_day",
    "window_end_day", "month", "end_date", "total_occurrences", "remind_days_before", "reminders",
    "is_subscription",
)  # fmt: skip


COPIED = tuple(name for name in EDITABLE if name not in ("amount", "category_id"))


def update_rule(
    db: Session, user: User, rule_id: uuid.UUID, version: int, changes: dict[str, Any]
) -> RecurringRule:
    """R7: forward only. Frequency, kind, start date and debt don't change; make a new rule."""
    rule = owned_rule(db, user, rule_id, lock=True)
    _check_version(rule, version)
    if rule.status == "ended":
        raise ApiError(409, "invalid_state", "An ended rule can't be changed")
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise ApiError(422, "validation_error", "Those fields can't be changed")
    draft = RuleDraft(
        kind=rule.kind,
        name=changes.get("name", rule.name),
        notes=changes.get("notes", rule.notes),
        amount=None,
        currency=changes.get("currency", rule.currency),
        account_id=changes.get("account_id", rule.account_id),
        category_id=changes.get("category_id", rule.category_id),
        debt_id=rule.debt_id,
        frequency=rule.frequency,
        window_start_day=changes.get("window_start_day", rule.window_start_day),
        window_end_day=changes.get("window_end_day", rule.window_end_day),
        month=changes.get("month", rule.month),
        start_date=rule.start_date,
        end_date=changes.get("end_date", rule.end_date),
        total_occurrences=changes.get("total_occurrences", rule.total_occurrences),
        remind_days_before=changes.get("remind_days_before", rule.remind_days_before),
        reminders=changes.get("reminders", rule.reminders),
        is_subscription=changes.get("is_subscription", rule.is_subscription),
    )
    _check_schedule(draft)
    category, _ = _check_links(db, user, draft)
    if "amount" in changes:
        draft.amount = changes["amount"]
        rule.amount_minor = _amount(db, draft)
    elif "currency" in changes and changes["currency"] != rule.currency:
        raise ApiError(422, "validation_error", "Changing the currency needs the amount too")
    for name in COPIED:
        setattr(rule, name, getattr(draft, name))
    if category is not None:
        rule.category_id, rule.category_kind = category.id, category.kind
    rule.version += 1
    db.flush()
    today = today_for(user)
    _drop_future(db, rule, today)
    generate(db, rule, today)
    audit(db, "recurring.rule_changed", user.id, entity_type="recurring_rule", entity_id=rule.id,
          fields=",".join(sorted(changes)))  # fmt: skip
    return rule


def set_status(
    db: Session, user: User, rule_id: uuid.UUID, version: int, status: str
) -> RecurringRule:
    rule = owned_rule(db, user, rule_id, lock=True)
    _check_version(rule, version)
    today = today_for(user)
    if rule.status == "ended":
        raise ApiError(409, "invalid_state", "An ended rule stays ended")
    if status == "paused":
        if rule.status != "active":
            raise ApiError(409, "invalid_state", "Only an active rule can be paused")
        _drop_future(db, rule, today)
    elif status == "active":
        if rule.status != "paused":
            raise ApiError(409, "invalid_state", "Only a paused rule can be resumed")
        # R8: from tomorrow on; windows missed while paused aren't invented as overdue.
        rule.generate_from = max(rule.generate_from, today + timedelta(days=1))
    elif status == "ended":
        _drop_future(db, rule, today)
        end = today if rule.end_date is None or rule.end_date > today else rule.end_date
        rule.end_date = max(end, rule.start_date)
    else:
        raise ApiError(422, "validation_error", "Unknown status")
    rule.status = status
    rule.version += 1
    db.flush()
    if status == "active":
        generate(db, rule, today)
    audit(db, f"recurring.rule_{status}", user.id, entity_type="recurring_rule", entity_id=rule.id)
    return rule


def delete_rule(db: Session, user: User, rule_id: uuid.UUID, version: int) -> None:
    """Removes a rule set up by mistake. Only when none of its payments was ever confirmed: then
    it never moved money, and only expectations go. A rule with confirmed payments can be ended
    (its history stays)."""
    rule = owned_rule(db, user, rule_id, lock=True)
    _check_version(rule, version)
    confirmed = db.scalar(
        select(func.count())
        .select_from(RecurringOccurrence)
        .where(
            RecurringOccurrence.rule_id == rule.id,
            RecurringOccurrence.transaction_id.is_not(None),
        )
    )
    # A confirmation voided later reopens its occurrence; the voided entry still names the rule's
    # account and category, but not the rule, so it doesn't block this.
    if confirmed:
        raise ApiError(409, "rule_has_payments", "Payments were recorded for this; end it instead")
    db.execute(delete(RecurringOccurrence).where(RecurringOccurrence.rule_id == rule.id))
    db.delete(rule)
    db.flush()
    audit(db, "recurring.rule_deleted", user.id, entity_type="recurring_rule", entity_id=rule_id)


# --- Occurrences


def generate(db: Session, rule: RecurringRule, today: date) -> None:
    """Idempotent: creates any missing occurrence up to 35 days ahead, and nothing else."""
    if rule.status != "active":
        return
    rows = [
        {
            "user_id": rule.user_id,
            "rule_id": rule.id,
            "sequence": window.sequence,
            "window_start": window.start,
            "window_end": window.end,
            "expected_amount_minor": rule.amount_minor,
            "currency": rule.currency,
            "account_id": rule.account_id,
        }
        for window in schedule.windows(
            plan_of(rule),
            ends_on_or_after=rule.generate_from,
            starts_on_or_before=today + timedelta(days=HORIZON_DAYS),
        )
    ]
    if rows:
        db.execute(insert(RecurringOccurrence).values(rows).on_conflict_do_nothing())


def ensure(db: Session, user: User) -> date:
    """Brings every active rule's occurrences up to date. Returns the user's today."""
    today = today_for(user)
    for rule in db.scalars(
        select(RecurringRule).where(
            RecurringRule.user_id == user.id, RecurringRule.status == "active"
        )
    ):
        generate(db, rule, today)
    db.flush()
    return today


def owned_occurrence(
    db: Session, user: User, occurrence_id: uuid.UUID, *, lock: bool = False
) -> RecurringOccurrence:
    query = select(RecurringOccurrence).where(
        RecurringOccurrence.id == occurrence_id, RecurringOccurrence.user_id == user.id
    )
    if lock:
        query = query.with_for_update()
    found = db.scalar(query)
    if found is None:
        raise not_found()
    return found


def _decided(occurrence: RecurringOccurrence) -> ApiError:
    return ApiError(
        409,
        "invalid_state",
        "This occurrence was already answered",
        {"status": occurrence.status},
    )


@dataclass(frozen=True)
class Confirmation:
    amount: str
    currency: str
    account_id: uuid.UUID | None
    effective_date: date | None
    category_id: uuid.UUID | None
    description: str | None
    conversion: conversion.Conversion | None
    account_amount: str | None  # debts across currencies


def confirm(
    db: Session,
    gateway: fx.FxGateway,
    user: User,
    occurrence_id: uuid.UUID,
    answer: Confirmation,
) -> tuple[RecurringOccurrence, Transaction, list[str]]:
    """Received / Paid: posts exactly one transaction with the actual amount, date and account."""
    occurrence = owned_occurrence(db, user, occurrence_id, lock=True)
    if occurrence.status != "pending":
        raise _decided(occurrence)
    rule = owned_rule(db, user, occurrence.rule_id)
    account_id = answer.account_id or occurrence.account_id
    warnings: list[str] = []
    if rule.kind == "debt_payment":
        if rule.debt_id is None:
            raise ApiError(409, "invalid_state", "This rule has no debt")
        document = debts.pay(
            db,
            user,
            rule.debt_id,
            debts.Payment(
                account_id=account_id,
                amount=answer.amount,
                account_amount=answer.account_amount,
                effective_date=answer.effective_date,
                description=answer.description or rule.name,
            ),
            via="recurring",
        )
    else:
        recorded = transactions.create(
            db,
            user,
            transactions.Draft(
                type=rule.kind,
                account_id=account_id,
                amount=answer.amount,
                currency=answer.currency,
                category_id=_category_for(rule, answer),
                effective_date=answer.effective_date,
                description=answer.description or rule.name,
                notes=None,
                conversion=answer.conversion,
            ),
            via="recurring",
            gateway=gateway,
        )
        document, warnings = recorded.transaction, recorded.warnings
    occurrence.status = "confirmed"
    occurrence.transaction_id = document.id
    occurrence.decided_at = datetime.now(UTC)
    occurrence.snoozed_until = None
    occurrence.version += 1
    db.flush()
    audit(db, "recurring.confirmed", user.id, entity_type="recurring_occurrence",
          entity_id=occurrence.id)  # fmt: skip
    return occurrence, document, warnings


def _category_for(rule: RecurringRule, answer: Confirmation) -> uuid.UUID:
    chosen = answer.category_id or rule.category_id
    if chosen is None:
        raise ApiError(422, "validation_error", "Choose a category")
    return chosen


def skip(db: Session, user: User, occurrence_id: uuid.UUID) -> RecurringOccurrence:
    occurrence = owned_occurrence(db, user, occurrence_id, lock=True)
    if occurrence.status != "pending":
        raise _decided(occurrence)
    occurrence.status = "skipped"
    occurrence.decided_at = datetime.now(UTC)
    occurrence.version += 1
    db.flush()
    audit(
        db,
        "recurring.skipped",
        user.id,
        entity_type="recurring_occurrence",
        entity_id=occurrence.id,
    )
    return occurrence


def not_yet(db: Session, user: User, occurrence_id: uuid.UUID) -> RecurringOccurrence:
    """Nothing is recorded; the reminder rests until tomorrow. It still becomes overdue."""
    occurrence = owned_occurrence(db, user, occurrence_id, lock=True)
    if occurrence.status != "pending":
        raise _decided(occurrence)
    occurrence.snoozed_until = today_for(user) + timedelta(days=1)
    occurrence.version += 1
    db.flush()
    return occurrence


def occurrences(
    db: Session,
    user: User,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    rule_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[tuple[RecurringOccurrence, RecurringRule]]:
    query = (
        select(RecurringOccurrence, RecurringRule)
        .join(RecurringRule, RecurringRule.id == RecurringOccurrence.rule_id)
        .where(RecurringOccurrence.user_id == user.id)
    )
    if date_from is not None:
        query = query.where(RecurringOccurrence.window_end >= date_from)
    if date_to is not None:
        query = query.where(RecurringOccurrence.window_start <= date_to)
    if rule_id is not None:
        query = query.where(RecurringOccurrence.rule_id == rule_id)
    query = query.order_by(RecurringOccurrence.window_start, RecurringOccurrence.id).limit(limit)
    return [(o, r) for o, r in db.execute(query).all()]


def upcoming(
    db: Session, user: User, today: date, *, limit: int = 4
) -> list[tuple[RecurringOccurrence, RecurringRule]]:
    """For Home: the nearest pending items — overdue and open ones first, then the next 14 days.
    A "Not yet" answer rests the reminder until tomorrow."""
    query = (
        select(RecurringOccurrence, RecurringRule)
        .join(RecurringRule, RecurringRule.id == RecurringOccurrence.rule_id)
        .where(
            RecurringOccurrence.user_id == user.id,
            RecurringOccurrence.status == "pending",
            RecurringOccurrence.window_start <= today + timedelta(days=14),
            RecurringRule.reminders.is_(True),
            (RecurringOccurrence.snoozed_until.is_(None))
            | (RecurringOccurrence.snoozed_until <= today),
        )
        .order_by(RecurringOccurrence.window_start, RecurringOccurrence.id)
        .limit(limit)
    )
    return [(o, r) for o, r in db.execute(query).all()]
