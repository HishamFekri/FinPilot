"""Categories (FINANCIAL_RULES.md §5, as revised for A4).

Every user has their own rows. The defaults are seeded once per user, each with a `system_key` that
the apps translate; renaming one stores a `name`, which then wins. Custom categories have only a
`name`. Archiving hides a category from pickers and keeps it on every past transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import Category, Transaction, User
from app.services.sessions import audit


@dataclass(frozen=True)
class Default:
    key: str
    kind: str
    icon: str
    color: str


# The approved taxonomy (PRD.md §3.4, A4 brief), in the picker's order. Icons are Lucide names;
# colors are palette names that the web app maps to light and dark values.
DEFAULTS: tuple[Default, ...] = (
    Default("food_and_drinks", "expense", "utensils", "tangerine"),
    Default("groceries", "expense", "shopping-basket", "green"),
    Default("transportation", "expense", "bus", "sky"),
    Default("shopping", "expense", "shopping-bag", "berry"),
    Default("housing", "expense", "house", "indigo"),
    Default("bills_utilities", "expense", "receipt", "amber"),
    Default("health", "expense", "heart-pulse", "rose"),
    Default("education", "expense", "graduation-cap", "violet"),
    Default("entertainment", "expense", "clapperboard", "teal"),
    Default("travel", "expense", "plane", "ocean"),
    Default("personal_care", "expense", "sparkles", "rose"),
    Default("subscriptions", "expense", "repeat", "slate"),
    Default("gifts", "expense", "gift", "berry"),
    # Interest and fees on installments and loans (FINANCIAL_RULES.md §4.13), added in 0016.
    Default("interest_fees", "expense", "landmark", "slate"),
    Default("other_expense", "expense", "circle-ellipsis", "sand"),
    Default("salary", "income", "briefcase", "green"),
    Default("freelance_business", "income", "laptop", "teal"),
    Default("bonus", "income", "award", "amber"),
    Default("gift_received", "income", "gift", "berry"),
    Default("other_income", "income", "circle-plus", "sand"),
)

# What custom categories may use: the same set the web app can draw.
ICONS = frozenset({d.icon for d in DEFAULTS}) | {
    "coffee", "car", "fuel", "baby", "dog", "dumbbell", "music", "book", "smartphone", "wifi",
    "zap", "droplet", "shirt", "wrench", "piggy-bank", "landmark", "hand-coins", "tag",
}  # fmt: skip
COLORS = frozenset(
    {"tangerine", "amber", "green", "teal", "sky", "ocean", "indigo", "violet", "berry", "rose",
     "slate", "sand"}
)  # fmt: skip
NAME_INDEX = "uq_categories_active_name"


def seed_defaults(db: Session, user_id: uuid.UUID) -> None:
    """Inserts any missing default for the user. Repeating it, even concurrently, adds nothing."""
    db.execute(
        insert(Category)
        .values(
            [
                {
                    "user_id": user_id,
                    "kind": d.kind,
                    "system_key": d.key,
                    "icon": d.icon,
                    "color": d.color,
                    "sort_order": index,
                }
                for index, d in enumerate(DEFAULTS)
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "system_key"],
            index_where=Category.system_key.is_not(None),
        )
    )


@dataclass(frozen=True)
class CategoryView:
    category: Category
    last_used_at: datetime | None


def list_for(db: Session, user: User, *, include_archived: bool) -> list[CategoryView]:
    """The user's categories, with when each was last used, for "most recent first" pickers."""
    last_used = (
        select(Transaction.category_id, func.max(Transaction.created_at).label("last_used"))
        .where(Transaction.user_id == user.id, Transaction.status == "posted")
        .group_by(Transaction.category_id)
        .subquery()
    )
    query = (
        select(Category, last_used.c.last_used)
        .outerjoin(last_used, last_used.c.category_id == Category.id)
        .where(Category.user_id == user.id)
        .order_by(Category.kind, Category.sort_order, Category.created_at, Category.id)
    )
    if not include_archived:
        query = query.where(Category.status == "active")
    return [
        CategoryView(cast(Category, row[0]), cast(datetime | None, row[1]))
        for row in db.execute(query)
    ]


def owned(db: Session, user: User, category_id: uuid.UUID, *, lock: bool = False) -> Category:
    query = select(Category).where(Category.id == category_id, Category.user_id == user.id)
    if lock:
        query = query.with_for_update()
    category = db.scalar(query)
    if category is None:
        raise ApiError(404, "not_found", "Not found")
    return category


def _taken(error: IntegrityError) -> bool:
    return NAME_INDEX in str(error.orig)


def _check_style(icon: str | None, color: str | None) -> None:
    if icon is not None and icon not in ICONS:
        raise ApiError(422, "invalid_icon", "That icon isn't available")
    if color is not None and color not in COLORS:
        raise ApiError(422, "invalid_color", "That color isn't available")


def create(db: Session, user: User, *, kind: str, name: str, icon: str, color: str) -> Category:
    _check_style(icon, color)
    category = Category(
        user_id=user.id, kind=kind, name=name, icon=icon, color=color, sort_order=200
    )
    try:
        with db.begin_nested():
            db.add(category)
            db.flush()
    except IntegrityError as error:
        if _taken(error):
            raise ApiError(409, "category_name_taken", "A category has this name") from None
        raise
    audit(db, "category.created", user.id, entity_type="category", entity_id=category.id)
    return category


def change(
    db: Session, user: User, category_id: uuid.UUID, version: int, changes: dict[str, object]
) -> Category:
    """Rename, restyle, archive, or restore. A seeded category's name can be reset to its default
    (translated) name with `name: null`; a custom one always needs a name."""
    category = owned(db, user, category_id)
    if "name" in changes and changes["name"] is None and category.system_key is None:
        raise ApiError(422, "validation_error", "A custom category needs a name")
    _check_style(changes.get("icon"), changes.get("color"))  # type: ignore[arg-type]
    try:
        with db.begin_nested():
            done = db.execute(
                update(Category)
                .where(
                    Category.id == category_id,
                    Category.user_id == user.id,
                    Category.version == version,
                )
                .values(version=Category.version + 1, **changes)
                .returning(Category.id)
            ).first()
    except IntegrityError as error:
        if _taken(error):
            raise ApiError(409, "category_name_taken", "A category has this name") from None
        raise
    if done is None:
        raise ApiError(412, "version_conflict", "The category changed; reload it")
    db.refresh(category)
    audit(
        db,
        "category.updated",
        user.id,
        entity_type="category",
        entity_id=category.id,
        fields=",".join(sorted(changes)),
    )
    return category


def delete(db: Session, user: User, category_id: uuid.UUID) -> None:
    """Removes a custom category that nothing has ever used (§5). A default category, or one
    used by any entry (even a voided one), budget, recurring item or template, can only be
    archived: history keeps pointing at it."""
    from app.db.models import Budget, JournalEntry, QuickTemplate, RecurringRule

    category = owned(db, user, category_id, lock=True)
    if category.system_key is not None:
        raise ApiError(
            409, "category_is_default", "Default categories can be archived, not deleted"
        )
    checks = (
        (Transaction, Transaction.category_id, "category_has_entries"),
        (JournalEntry, JournalEntry.category_id, "category_has_entries"),
        (Budget, Budget.category_id, "category_used_by_budget"),
        (RecurringRule, RecurringRule.category_id, "category_used_by_recurring"),
        (QuickTemplate, QuickTemplate.category_id, "category_used_by_template"),
    )
    for model, column, code in checks:
        if db.scalar(select(func.count()).select_from(model).where(column == category.id)):
            raise ApiError(409, code, "This category is in use; archive it instead")
    db.delete(category)
    db.flush()
    audit(db, "category.deleted", user.id, entity_type="category", entity_id=category_id)
