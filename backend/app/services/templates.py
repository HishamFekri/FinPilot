"""Quick templates: saved shortcuts for transactions the person enters often (PRD §3.4).

A template never posts anything. Applying one opens Quick Add with its details and today's date;
the person reviews them and saves through the ordinary transaction route, which does every check.
A template isn't a schedule (that's Recurring), and deleting one changes no financial record.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import FinancialAccount, QuickTemplate, Transaction, User
from app.services import money
from app.services.accounts import clean_text
from app.services.categories import owned as owned_category
from app.services.sessions import audit

MAX_TEMPLATES = 100


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Template not found")


@dataclass(frozen=True)
class TemplateDraft:
    name: str
    kind: str
    category_id: uuid.UUID
    currency: str
    amount: str | None = None  # None: asked each time
    account_id: uuid.UUID | None = None  # None: the default account at the time
    description: str | None = None
    is_favourite: bool = False


def owned(db: Session, user: User, template_id: uuid.UUID, *, lock: bool = False) -> QuickTemplate:
    query = select(QuickTemplate).where(
        QuickTemplate.id == template_id, QuickTemplate.user_id == user.id
    )
    found = db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise not_found()
    return found


def list_for(db: Session, user: User, *, include_archived: bool = False) -> list[QuickTemplate]:
    """Favourites first, then the most used, then by name."""
    query = select(QuickTemplate).where(QuickTemplate.user_id == user.id)
    if not include_archived:
        query = query.where(QuickTemplate.status == "active")
    return list(
        db.scalars(
            query.order_by(
                QuickTemplate.is_favourite.desc(),
                QuickTemplate.use_count.desc(),
                QuickTemplate.name,
            )
        )
    )


def _check(db: Session, user: User, draft: TemplateDraft) -> int | None:
    if draft.kind not in ("income", "expense"):
        raise ApiError(422, "validation_error", "A template is an income or an expense")
    category = owned_category(db, user, draft.category_id)
    if category.kind != draft.kind:
        raise ApiError(422, "category_kind_mismatch", "Choose a category of the same kind")
    if category.status != "active":
        raise ApiError(409, "category_archived", "An archived category can't be used")
    if draft.account_id is not None:
        account = db.scalar(
            select(FinancialAccount).where(
                FinancialAccount.id == draft.account_id, FinancialAccount.user_id == user.id
            )
        )
        if account is None:
            raise ApiError(404, "not_found", "Account not found")
        if account.currency != draft.currency:
            raise ApiError(
                422,
                "currency_mismatch",
                "The amount must be in the account's currency",
                {"account_currency": account.currency},
            )
    if draft.amount is None:
        money.currency(db, draft.currency)  # a known currency
        return None
    minor, _ = money.parse(db, draft.amount, draft.currency)
    if minor <= 0:
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    return minor


def _text(value: str | None, limit: int) -> str | None:
    return clean_text(value, max_length=limit) if value and value.strip() else None


def create(db: Session, user: User, draft: TemplateDraft) -> QuickTemplate:
    count = len(list_for(db, user, include_archived=True))
    if count >= MAX_TEMPLATES:
        raise ApiError(409, "too_many_templates", "You have the most templates allowed")
    amount = _check(db, user, draft)
    row = QuickTemplate(
        user_id=user.id,
        name=clean_text(draft.name, max_length=60),
        kind=draft.kind,
        amount_minor=amount,
        currency=draft.currency,
        account_id=draft.account_id,
        category_id=draft.category_id,
        description=_text(draft.description, 200),
        is_favourite=draft.is_favourite,
    )
    db.add(row)
    db.flush()
    audit(db, "template.created", user.id, entity_type="quick_template", entity_id=row.id)
    return row


def from_transaction(
    db: Session, user: User, transaction_id: uuid.UUID, name: str | None, favourite: bool
) -> QuickTemplate:
    """Saves an income or expense as a template: its account, category, amount and text."""
    txn = db.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user.id)
    )
    if txn is None:
        raise ApiError(404, "not_found", "Not found")
    if txn.type not in ("income", "expense") or txn.category_id is None:
        raise ApiError(422, "validation_error", "Only an income or an expense can be a template")
    exponent = money.currency(db, txn.currency, for_new_records=False).exponent
    label = (name or txn.description or "").strip()
    if not label:
        raise ApiError(422, "validation_error", "Give the template a name")
    return create(
        db,
        user,
        TemplateDraft(
            name=label,
            kind=txn.type,
            category_id=txn.category_id,
            currency=txn.currency,
            amount=money.to_amount(txn.amount_minor, exponent),
            account_id=txn.account_id,
            description=txn.description,
            is_favourite=favourite,
        ),
    )


def update(
    db: Session, user: User, template_id: uuid.UUID, version: int, changes: dict[str, object]
) -> QuickTemplate:
    row = owned(db, user, template_id, lock=True)
    if row.version != version:
        raise ApiError(412, "version_conflict", "This template changed; reload and try again")
    exponent = money.currency(db, row.currency, for_new_records=False).exponent
    current = TemplateDraft(
        name=row.name,
        kind=row.kind,
        category_id=row.category_id,
        currency=row.currency,
        amount=money.to_amount(row.amount_minor, exponent) if row.amount_minor else None,
        account_id=row.account_id,
        description=row.description,
        is_favourite=row.is_favourite,
    )
    merged = TemplateDraft(
        **{**current.__dict__, **{k: v for k, v in changes.items() if k in current.__dict__}}
    )
    row.amount_minor = _check(db, user, merged)
    row.name = clean_text(merged.name, max_length=60)
    row.kind = merged.kind
    row.category_id = merged.category_id
    row.currency = merged.currency
    row.account_id = merged.account_id
    row.description = _text(merged.description, 200)
    row.is_favourite = merged.is_favourite
    if "status" in changes:
        if changes["status"] not in ("active", "archived"):
            raise ApiError(422, "validation_error", "Choose active or archived")
        row.status = str(changes["status"])
    row.version += 1
    db.flush()
    audit(db, "template.updated", user.id, entity_type="quick_template", entity_id=row.id)
    return row


def delete(db: Session, user: User, template_id: uuid.UUID) -> None:
    """A template has no financial history, so deleting it removes only the shortcut."""
    row = owned(db, user, template_id, lock=True)
    db.delete(row)
    db.flush()
    audit(db, "template.deleted", user.id, entity_type="quick_template", entity_id=template_id)


def used(db: Session, user: User, template_id: uuid.UUID) -> QuickTemplate:
    """Counts a use after the person saved a transaction from it. Posts nothing."""
    row = owned(db, user, template_id, lock=True)
    row.use_count += 1
    row.last_used_at = datetime.now(UTC)
    db.flush()
    return row
