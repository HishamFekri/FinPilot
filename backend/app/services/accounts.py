"""Financial accounts (API_SPEC.md §4, FINANCIAL_RULES.md §4.2, §4.9).

Every query is scoped to the signed-in user, so another user's account is simply not found (404).
Balances are derived from the ledger; there is no way to set one directly. An opening balance is a
real `opening_balance` transaction against Equity:Opening, posted in the same database transaction
as the account.
"""

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, update
from sqlalchemy import delete as sql_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import ApiError, InvalidText
from app.db.models import FinancialAccount, LedgerAccount, Transaction, User
from app.services import ledger, money
from app.services.sessions import audit

NAME_UNIQUE_INDEX = "uq_financial_accounts_active_name"


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found")


def clean_text(value: str, *, max_length: int) -> str:
    """Trimmed, non-empty, within length, with no control or bidirectional-formatting characters.

    The database checks the same rule. Account names are shown to their owner next to amounts, so a
    hidden direction override could make an amount read differently (SECURITY.md §6).
    """
    value = value.strip()
    if not value or len(value) > max_length:
        raise InvalidText(f"must be 1 to {max_length} characters")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in value):
        raise InvalidText("contains characters that aren't allowed")
    return value


def today_for(user: User) -> date:
    """The user's local calendar date, from their time zone setting (FINANCIAL_RULES.md T1)."""
    try:
        zone = ZoneInfo(user.time_zone)
    except (KeyError, ValueError):
        zone = ZoneInfo("UTC")
    return clock.today(zone)


@dataclass(frozen=True)
class AccountView:
    account: FinancialAccount
    balance_minor: int


@dataclass(frozen=True)
class NewAccount:
    name: str
    description: str | None
    type: str
    currency: str
    opened_on: date | None
    opening_amount: str | None
    opening_currency: str | None


def _owned(
    db: Session, user: User, account_id: uuid.UUID, *, lock: bool = False
) -> FinancialAccount:
    query = select(FinancialAccount).where(
        FinancialAccount.id == account_id, FinancialAccount.user_id == user.id
    )
    if lock:
        query = query.with_for_update()
    account = db.scalar(query)
    if account is None:
        raise not_found()
    return account


def _name_taken(error: IntegrityError) -> bool:
    return NAME_UNIQUE_INDEX in str(error.orig)


def _view(db: Session, user: User, account: FinancialAccount) -> AccountView:
    return AccountView(account, ledger.balances(db, user.id, [account.id])[account.id])


def create(db: Session, user: User, new: NewAccount) -> AccountView:
    info = money.currency(db, new.currency)
    today = today_for(user)
    opened_on = new.opened_on or today
    if opened_on > today:
        raise ApiError(422, "date_in_future", "opened_on can't be in the future")

    opening_minor = 0
    if new.opening_amount is not None:
        if (new.opening_currency or info.code) != info.code:
            raise ApiError(
                422, "currency_mismatch", "The opening balance must be in the account's currency"
            )
        opening_minor, _ = money.parse(db, new.opening_amount, info.code)

    account = FinancialAccount(
        user_id=user.id,
        name=new.name,
        description=new.description,
        type=new.type,
        currency=info.code,
        opened_on=opened_on,
    )
    try:
        with db.begin_nested():
            db.add(account)
            db.flush()
    except IntegrityError as error:
        if _name_taken(error):
            raise ApiError(409, "account_name_taken", "An active account has this name") from None
        raise
    asset = ledger.asset_account(db, account)

    # A zero opening balance posts nothing: a journal line can't be zero (DATABASE.md §3.3).
    if opening_minor != 0:
        opening = Transaction(
            user_id=user.id,
            type="opening_balance",
            account_id=account.id,
            amount_minor=abs(opening_minor),
            direction="increase" if opening_minor > 0 else "decrease",
            currency=info.code,
            occurred_at=datetime.combine(opened_on, time.min, tzinfo=UTC),
            effective_date=opened_on,
            created_via="form",
        )
        db.add(opening)
        db.flush()
        equity = ledger.shared_account(db, user.id, "equity_opening", info.code)
        ledger.post(
            db,
            opening,
            [
                ledger.Line(asset, info.code, opening_minor),
                ledger.Line(equity, info.code, -opening_minor),
            ],
        )

    audit(db, "account.created", user.id, entity_type="financial_account", entity_id=account.id)
    return AccountView(account, opening_minor)


def list_for(db: Session, user: User, status: str | None) -> list[AccountView]:
    query = select(FinancialAccount).where(FinancialAccount.user_id == user.id)
    if status is not None:
        query = query.where(FinancialAccount.status == status)
    # Cash and Bank are created in the same instant: Cash comes first, then the order of creation.
    automatic = case(
        (FinancialAccount.system_key == "cash", 0),
        (FinancialAccount.system_key == "bank", 1),
        else_=2,
    )
    order = (FinancialAccount.created_at, automatic, FinancialAccount.id)
    accounts = list(db.scalars(query.order_by(*order)))
    totals = ledger.balances(db, user.id, [a.id for a in accounts])
    return [AccountView(a, totals[a.id]) for a in accounts]


def get(db: Session, user: User, account_id: uuid.UUID) -> AccountView:
    return _view(db, user, _owned(db, user, account_id))


def _bump(
    db: Session, user: User, account_id: uuid.UUID, expected_version: int, **changes: object
) -> FinancialAccount:
    """A conditional update on the version (optimistic concurrency, API_SPEC.md §1.7)."""
    try:
        with db.begin_nested():
            updated = db.execute(
                update(FinancialAccount)
                .where(
                    FinancialAccount.id == account_id,
                    FinancialAccount.user_id == user.id,
                    FinancialAccount.version == expected_version,
                )
                .values(version=FinancialAccount.version + 1, **changes)
                .returning(FinancialAccount.id)
            ).first()
    except IntegrityError as error:
        if _name_taken(error):
            raise ApiError(409, "account_name_taken", "An active account has this name") from None
        raise
    if updated is None:
        current = _owned(db, user, account_id)  # 404 if it isn't theirs
        raise ApiError(
            412, "version_conflict", "The account changed; reload it", {"version": current.version}
        )
    account = _owned(db, user, account_id)
    db.refresh(account)
    return account


def rename(
    db: Session, user: User, account_id: uuid.UUID, version: int, changes: dict[str, object]
) -> AccountView:
    account = _bump(db, user, account_id, version, **changes)
    audit(
        db,
        "account.updated",
        user.id,
        entity_type="financial_account",
        entity_id=account.id,
        fields=",".join(sorted(changes)),
    )
    return _view(db, user, account)


def archive(db: Session, user: User, account_id: uuid.UUID) -> AccountView:
    """Only at zero balance, so totals and reports always agree (FINANCIAL_RULES.md §4.9)."""
    account = _owned(db, user, account_id, lock=True)
    if account.status == "archived":
        return _view(db, user, account)
    balance = ledger.balances(db, user.id, [account.id])[account.id]
    if balance != 0:
        raise ApiError(409, "balance_not_zero", "Only an account at zero balance can be archived")
    account = _bump(db, user, account.id, account.version, status="archived")
    audit(db, "account.archived", user.id, entity_type="financial_account", entity_id=account.id)
    return AccountView(account, 0)


def unarchive(db: Session, user: User, account_id: uuid.UUID) -> AccountView:
    account = _owned(db, user, account_id, lock=True)
    if account.status == "active":
        return _view(db, user, account)
    account = _bump(db, user, account.id, account.version, status="active")
    audit(db, "account.unarchived", user.id, entity_type="financial_account", entity_id=account.id)
    return _view(db, user, account)


def delete(db: Session, user: User, account_id: uuid.UUID) -> None:
    """Only an account nothing was ever recorded on, and nothing else points at. Otherwise it can
    only be archived (§4.9). Being the Quick Add default isn't a reason to keep it: that
    preference is cleared."""
    from app.db.models import QuickTemplate, RecurringRule  # avoids a wide import at module level

    account = _owned(db, user, account_id, lock=True)
    if ledger.has_entries(db, account):
        raise ApiError(409, "account_has_entries", "An account with entries can only be archived")
    for model, code in ((RecurringRule, "account_used_by_recurring"),
                        (QuickTemplate, "account_used_by_template")):  # fmt: skip
        used = db.scalar(
            select(func.count()).select_from(model).where(model.account_id == account.id)
        )
        if used:
            raise ApiError(409, code, "Something still uses this account; change it first")
    if user.default_account_id == account.id:
        user.default_account_id = None
        db.flush()
    db.execute(sql_delete(LedgerAccount).where(LedgerAccount.financial_account_id == account.id))
    db.delete(account)
    db.flush()
    audit(db, "account.deleted", user.id, entity_type="financial_account", entity_id=account_id)
