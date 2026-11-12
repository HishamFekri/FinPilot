"""Category groups: a way to organise expense categories (PRD §3.4).

- Each expense category belongs to at most one group (`categories.group_id`), so a group's total is
  the sum of its categories' totals and nothing is counted twice; categories in no group are
  "Ungrouped".
- Defaults (Essentials, Lifestyle, Other) are seeded once per person, on first use, with the
  default categories placed in them. After that, the person's choices are kept.
- Groups only regroup totals that `reporting` already calculated; budgets, reports and history
  are unchanged. A group's breakdown uses today's grouping for every period (moves are audited).
- Archiving a group moves its categories to Ungrouped; nothing is deleted.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import Category, CategoryGroup, User
from app.services.accounts import clean_text
from app.services.reporting import CategoryTotal
from app.services.sessions import audit

DEFAULT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("essentials", ("groceries", "housing", "bills_utilities", "transportation", "health")),
    (
        "lifestyle",
        (
            "food_and_drinks",
            "entertainment",
            "shopping",
            "travel",
            "personal_care",
            "subscriptions",
            "gifts",
        ),
    ),
    ("other", ("education", "other_expense")),
)
MAX_GROUPS = 30


def ensure(db: Session, user: User) -> None:
    """Seeds the default groups the first time; never again once the person has any group."""
    if db.scalar(select(func.count()).where(CategoryGroup.user_id == user.id)):
        return
    for order, (key, _) in enumerate(DEFAULT_GROUPS):
        db.execute(
            insert(CategoryGroup)
            .values(user_id=user.id, system_key=key, sort_order=(order + 1) * 10)
            .on_conflict_do_nothing(
                index_elements=["user_id", "system_key"],
                index_where=CategoryGroup.system_key.is_not(None),
            )
        )
    groups = {
        g.system_key: g.id
        for g in db.scalars(select(CategoryGroup).where(CategoryGroup.user_id == user.id))
    }
    for key, members in DEFAULT_GROUPS:
        db.execute(
            update(Category)
            .where(
                Category.user_id == user.id,
                Category.kind == "expense",
                Category.group_id.is_(None),
                Category.system_key.in_(members),
            )
            .values(group_id=groups[key])
        )
    db.flush()


def list_for(db: Session, user: User, *, include_archived: bool = False) -> list[CategoryGroup]:
    ensure(db, user)
    query = select(CategoryGroup).where(CategoryGroup.user_id == user.id)
    if not include_archived:
        query = query.where(CategoryGroup.status == "active")
    return list(db.scalars(query.order_by(CategoryGroup.sort_order, CategoryGroup.created_at)))


def owned(db: Session, user: User, group_id: uuid.UUID, *, lock: bool = False) -> CategoryGroup:
    query = select(CategoryGroup).where(
        CategoryGroup.id == group_id, CategoryGroup.user_id == user.id
    )
    found = db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise ApiError(404, "not_found", "Group not found")
    return found


def _flush_named(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as error:
        raise ApiError(
            409, "group_name_taken", "You already have a group with that name"
        ) from error


def create(db: Session, user: User, name: str) -> CategoryGroup:
    ensure(db, user)
    if len(list_for(db, user, include_archived=True)) >= MAX_GROUPS:
        raise ApiError(409, "too_many_groups", "You have the most groups allowed")
    row = CategoryGroup(user_id=user.id, name=clean_text(name, max_length=40), sort_order=500)
    db.add(row)
    _flush_named(db)
    audit(db, "category_group.created", user.id, entity_type="category_group", entity_id=row.id)
    return row


def change(
    db: Session,
    user: User,
    group_id: uuid.UUID,
    version: int,
    *,
    name: str | None = None,
    status: str | None = None,
) -> CategoryGroup:
    row = owned(db, user, group_id, lock=True)
    if row.version != version:
        raise ApiError(412, "version_conflict", "This group changed; reload and try again")
    if name is not None:
        row.name = clean_text(name, max_length=40)
    if status is not None and status != row.status:
        if status not in ("active", "archived"):
            raise ApiError(422, "validation_error", "Choose active or archived")
        row.status = status
        if status == "archived":
            # Safe archiving: its categories become ungrouped; every figure stays the same.
            db.execute(
                update(Category)
                .where(Category.user_id == user.id, Category.group_id == row.id)
                .values(group_id=None)
            )
    row.version += 1
    _flush_named(db)
    audit(db, "category_group.updated", user.id, entity_type="category_group", entity_id=row.id)
    return row


def move(db: Session, user: User, category_id: uuid.UUID, group_id: uuid.UUID | None) -> Category:
    """Puts an expense category in a group (or none). Changes no amount anywhere."""
    category = db.scalar(
        select(Category)
        .where(Category.id == category_id, Category.user_id == user.id)
        .with_for_update()
    )
    if category is None:
        raise ApiError(404, "not_found", "Category not found")
    if category.kind != "expense":
        raise ApiError(422, "validation_error", "Only expense categories are grouped")
    if group_id is not None:
        group = owned(db, user, group_id)
        if group.status != "active":
            raise ApiError(409, "group_archived", "Choose an active group")
    category.group_id = group_id
    category.version += 1
    db.flush()
    audit(db, "category.group_changed", user.id, entity_type="category", entity_id=category.id,
          group=str(group_id) if group_id else "none")  # fmt: skip
    return category


@dataclass(frozen=True)
class GroupTotal:
    currency: str
    group_id: uuid.UUID | None  # None: ungrouped
    amount: int


def totals(db: Session, user: User, categories: list[CategoryTotal]) -> list[GroupTotal]:
    """Spending per group and currency from per-category totals: each category counted once."""
    ensure(db, user)
    group_of: dict[uuid.UUID, uuid.UUID | None] = {}
    query = select(Category.id, Category.group_id).where(Category.user_id == user.id)
    for category_id, group_id in db.execute(query):
        group_of[category_id] = group_id
    sums: dict[tuple[str, uuid.UUID | None], int] = defaultdict(int)
    for c in categories:
        if c.kind != "expense":
            continue
        group = group_of.get(c.category_id) if c.category_id else None
        sums[(c.currency, group)] += c.amount
    out = [GroupTotal(code, gid, amount) for (code, gid), amount in sums.items() if amount]
    return sorted(out, key=lambda g: (g.currency, -g.amount, str(g.group_id)))
