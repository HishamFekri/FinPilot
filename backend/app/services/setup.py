"""One-time setup: default accounts, default categories, and the default account (A4).

A new user should record an expense right away, without creating anything first. Setup runs once
per user, on their first visit to the app:

- **Categories:** the defaults are seeded (once per user and key, by unique index).
- **Accounts:** if the user has none, a Cash and a Bank account are created in their preferred
  currency, at zero, with no journal entry (a zero opening balance posts nothing). A user who
  already has accounts keeps exactly what they have; nothing is added or replaced.
- **Default account:** Cash, or for an existing user, their earliest active account.

Safety under refreshes and concurrent requests: the user's row is locked (`FOR UPDATE`) for the
whole setup, `setup_completed_at` makes every later run a no-op, and the unique indexes on
`(user_id, system_key)` would refuse a duplicate even if both of those failed.

The only thing it can need from the person is a currency, and only when none is saved.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import FinancialAccount, User
from app.services import categories, ledger, money
from app.services.accounts import today_for
from app.services.sessions import audit

DEFAULT_ACCOUNTS = (("cash", "cash"), ("bank", "bank"))  # (system_key, type)


@dataclass(frozen=True)
class SetupState:
    completed: bool
    needs_currency: bool


def state(db: Session, user: User) -> SetupState:
    if user.setup_completed_at is not None:
        return SetupState(completed=True, needs_currency=False)
    has_accounts = (
        db.scalar(select(func.count()).where(FinancialAccount.user_id == user.id)) or 0
    ) > 0
    return SetupState(
        completed=False, needs_currency=not has_accounts and user.preferred_currency is None
    )


def run(db: Session, user: User, currency: str | None) -> None:
    """Completes setup for the user, exactly once. Safe to call any number of times."""
    db.refresh(user, with_for_update=True)
    if user.setup_completed_at is not None:
        return

    categories.seed_defaults(db, user.id)

    has_accounts = (
        db.scalar(select(func.count()).where(FinancialAccount.user_id == user.id)) or 0
    ) > 0
    if not has_accounts:
        code = user.preferred_currency or currency
        if code is None:
            raise ApiError(409, "currency_required", "Choose a currency to start with")
        info = money.currency(db, code)
        if user.preferred_currency is None:
            user.preferred_currency = info.code
        opened = today_for(user)
        db.execute(
            insert(FinancialAccount)
            .values(
                [
                    {
                        "user_id": user.id,
                        "name": None,
                        "system_key": key,
                        "type": kind,
                        "currency": info.code,
                        "opened_on": opened,
                    }
                    for key, kind in DEFAULT_ACCOUNTS
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "system_key"],
                index_where=FinancialAccount.system_key.is_not(None),
            )
        )
        for account in db.scalars(
            select(FinancialAccount).where(
                FinancialAccount.user_id == user.id, FinancialAccount.system_key.is_not(None)
            )
        ):
            ledger.asset_account(db, account)

    if user.default_account_id is None:
        fallback = fallback_account(db, user.id)
        user.default_account_id = fallback.id if fallback else None
    user.setup_completed_at = datetime.now(UTC)
    db.flush()
    audit(db, "user.setup_completed", user.id, created_defaults=str(not has_accounts).lower())


def fallback_account(db: Session, user_id: uuid.UUID) -> FinancialAccount | None:
    """The documented fallback when there's no valid default: the active Cash account, then
    Bank, then the earliest other active account. Never an archived one."""
    preference = case(
        (FinancialAccount.system_key == "cash", 0),
        (FinancialAccount.system_key == "bank", 1),
        else_=2,
    )
    return db.scalar(
        select(FinancialAccount)
        .where(FinancialAccount.user_id == user_id, FinancialAccount.status == "active")
        .order_by(preference, FinancialAccount.created_at, FinancialAccount.id)
        .limit(1)
    )


def default_account(db: Session, user: User) -> FinancialAccount | None:
    """The account Quick Add starts with: the chosen default if it's still active, otherwise the
    fallback. Reading it never changes the saved preference."""
    if user.default_account_id is not None:
        chosen = db.scalar(
            select(FinancialAccount).where(
                FinancialAccount.id == user.default_account_id,
                FinancialAccount.user_id == user.id,
                FinancialAccount.status == "active",
            )
        )
        if chosen is not None:
            return chosen
    return fallback_account(db, user.id)


def set_default(db: Session, user: User, account_id: uuid.UUID) -> FinancialAccount:
    account = db.scalar(
        select(FinancialAccount).where(
            FinancialAccount.id == account_id, FinancialAccount.user_id == user.id
        )
    )
    if account is None:
        raise ApiError(404, "not_found", "Not found")
    if account.status != "active":
        raise ApiError(409, "account_archived", "An archived account can't be the default")
    user.default_account_id = account.id
    db.flush()
    audit(db, "user.default_account_changed", user.id, entity_type="financial_account",
          entity_id=account.id)  # fmt: skip
    return account
