"""Installment plans (FINANCIAL_RULES.md §4.13): a purchase or loan paid back on a schedule.

Built on what exists, so no new posting rule is needed:

- **The debt** (§4.10) holds what remains: its own `debt_payable` ledger account. Nothing stores a
  "remaining" figure.
- **A purchase** (the owner's decision, 2026-11-14: full price at purchase) posts two ordinary
  entries in one database transaction: an expense for the full price from the chosen account, and
  the existing "borrowed, money arrived" debt start into the same account for the financed part.
  The account loses only the down payment; spending shows the full price once, in the purchase
  month; the debt holds the financed part. A cash loan is only the debt start (never income). A plan
  that began before FinPilot records only what remains, against opening equity, so it's never
  spending.
- **A payment** is the existing debt repayment for the principal (never spending) plus, when the
  agreement states them, separate expenses for interest and fees in the "Interest & fees" category
  (the owner's decision, 2026-11-14: spending, own category). FinPilot never calculates interest.
- **The schedule** (`installment_items`) is an expectation, like a recurring occurrence (R1): it
  never posts and never changes a balance. Which installments are paid is worked out from the
  principal actually repaid (the debt's ledger), oldest first, so a partial payment shows as
  partial, an early or extra payment covers later installments, and voiding a payment undoes it.
- **Rescheduling** supersedes the installments not yet fully paid (rows are never edited or
  deleted; a trigger enforces it) and writes new ones for the same principal.
"""

import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    Category,
    Debt,
    FinancialAccount,
    InstallmentItem,
    InstallmentPayment,
    InstallmentPlan,
    SavingsGoal,
    Transaction,
    User,
)
from app.services import categories, debts, money, transactions
from app.services.accounts import clean_text, today_for
from app.services.movements import lock_accounts
from app.services.sessions import audit

INTEREST_KEY = "interest_fees"
MAX_PLANS = 100
ORIGINS = ("purchase", "loan", "existing")


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found")


def add_months(value: date, months: int, day: int) -> date:
    """The same day `months` later, on the month's last day when it's shorter (R6)."""
    index = value.year * 12 + value.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day, monthrange(year, month)[1]))


def split(total: int, count: int) -> list[int]:
    """Equal parts in minor units; the rounding remainder goes on the last one. Never invented."""
    base, remainder = divmod(total, count)
    return [base] * (count - 1) + [base + remainder]


def _positive(db: Session, text: str, currency: str, *, zero: bool = False) -> int:
    minor, _ = money.parse(db, text, currency)
    if minor < 0 or (minor == 0 and not zero):
        raise ApiError(422, "amount_not_positive", "The amount must be greater than zero")
    return minor


def _optional(db: Session, text: str | None, currency: str) -> int:
    return _positive(db, text, currency, zero=True) if text and text.strip() else 0


# --- Reading


@dataclass(frozen=True)
class ItemState:
    item: InstallmentItem
    covered_minor: int  # principal paid towards this installment
    state: str  # paid, partial, due, overdue, upcoming

    @property
    def total_minor(self) -> int:
        return self.item.principal_minor + self.item.interest_minor + self.item.fee_minor

    @property
    def outstanding_principal(self) -> int:
        return self.item.principal_minor - self.covered_minor


@dataclass
class PlanView:
    plan: InstallmentPlan
    debt: Debt
    principal_minor: int  # the debt's original amount (what FinPilot tracks)
    remaining_minor: int  # from the ledger
    items: list[ItemState]
    superseded: list[InstallmentItem]
    payments: list[InstallmentPayment]
    interest_paid_minor: int
    fees_paid_minor: int
    today: date
    goal: SavingsGoal | None = None
    transactions: dict[uuid.UUID, Transaction] = field(default_factory=dict)

    @property
    def paid_minor(self) -> int:
        return self.principal_minor - self.remaining_minor

    @property
    def paid_count(self) -> int:
        return self.plan.paid_before_count + sum(1 for i in self.items if i.state == "paid")

    @property
    def remaining_count(self) -> int:
        return sum(1 for i in self.items if i.state != "paid")

    @property
    def open_items(self) -> list[ItemState]:
        return [i for i in self.items if i.state != "paid"]

    @property
    def next_item(self) -> ItemState | None:
        return next(iter(self.open_items), None)

    @property
    def overdue(self) -> list[ItemState]:
        return [i for i in self.items if i.state == "overdue"]

    @property
    def completion_date(self) -> date | None:
        return self.items[-1].item.due_date if self.items else None

    @property
    def scheduled_interest_left(self) -> int:
        return sum(i.item.interest_minor + i.item.fee_minor for i in self.open_items)

    @property
    def settled(self) -> bool:
        return self.remaining_minor == 0

    @property
    def ahead_minor(self) -> int:
        """Principal repaid beyond what the schedule expected by today (never negative)."""
        due = sum(i.item.principal_minor for i in self.items if i.item.due_date <= self.today)
        return max(0, self.paid_minor - due)

    @property
    def behind_minor(self) -> int:
        due = sum(i.item.principal_minor for i in self.items if i.item.due_date <= self.today)
        return max(0, due - self.paid_minor)


def _states(items: list[InstallmentItem], paid: int, today: date) -> list[ItemState]:
    out = []
    left = paid
    for item in sorted(items, key=lambda i: i.sequence):
        covered = min(left, item.principal_minor)
        left -= covered
        if covered == item.principal_minor:
            state = "paid"
        elif item.due_date < today:
            state = "overdue"
        elif item.due_date == today:
            state = "due"
        elif covered > 0:
            state = "partial"
        else:
            state = "upcoming"
        out.append(ItemState(item, covered, state))
    return out


def owned(db: Session, user: User, plan_id: uuid.UUID, *, lock: bool = False) -> InstallmentPlan:
    query = select(InstallmentPlan).where(
        InstallmentPlan.id == plan_id, InstallmentPlan.user_id == user.id
    )
    if lock:
        query = query.with_for_update()
    found = db.scalar(query)
    if found is None:
        raise not_found()
    return found


def _posted_sum(db: Session, ids: list[uuid.UUID]) -> int:
    if not ids:
        return 0
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.id.in_(ids), Transaction.status == "posted"
            )
        )
        or 0
    )


def view(db: Session, user: User, plan: InstallmentPlan) -> PlanView:
    debt = db.get(Debt, plan.debt_id)
    assert debt is not None  # noqa: S101 - guaranteed by the foreign key
    rows = list(db.scalars(select(InstallmentItem).where(InstallmentItem.plan_id == plan.id)))
    live = [r for r in rows if r.superseded_at is None]
    superseded = sorted(
        (r for r in rows if r.superseded_at is not None),
        key=lambda r: (r.superseded_at or datetime.min.replace(tzinfo=UTC), r.sequence),
    )
    remaining = debts.remaining(db, debt)
    today = today_for(user)
    payments = list(
        db.scalars(
            select(InstallmentPayment)
            .where(InstallmentPayment.plan_id == plan.id)
            .order_by(InstallmentPayment.paid_on, InstallmentPayment.created_at)
        )
    )
    ids = [
        i
        for p in payments
        for i in (p.principal_transaction_id, p.interest_transaction_id, p.fee_transaction_id)
        if i is not None
    ]
    found = {t.id: t for t in db.scalars(select(Transaction).where(Transaction.id.in_(ids)))}
    return PlanView(
        plan=plan,
        debt=debt,
        principal_minor=debt.original_amount_minor,
        remaining_minor=remaining,
        items=_states(live, debt.original_amount_minor - remaining, today),
        superseded=superseded,
        payments=payments,
        interest_paid_minor=_posted_sum(
            db, [p.interest_transaction_id for p in payments if p.interest_transaction_id]
        ),
        fees_paid_minor=_posted_sum(
            db, [p.fee_transaction_id for p in payments if p.fee_transaction_id]
        ),
        today=today,
        goal=db.get(SavingsGoal, plan.goal_id) if plan.goal_id else None,
        transactions=found,
    )


def list_for(db: Session, user: User, *, include_archived: bool = False) -> list[PlanView]:
    query = (
        select(InstallmentPlan)
        .where(InstallmentPlan.user_id == user.id)
        .order_by(InstallmentPlan.created_at)
    )
    if not include_archived:
        query = query.where(InstallmentPlan.status == "active")
    return [view(db, user, p) for p in db.scalars(query)]


def for_goal(db: Session, goal_id: uuid.UUID) -> InstallmentPlan | None:
    return db.scalar(
        select(InstallmentPlan).where(
            InstallmentPlan.goal_id == goal_id, InstallmentPlan.status == "active"
        )
    )


# --- Creating


@dataclass(frozen=True)
class PlanDraft:
    origin: str  # purchase, loan, existing
    name: str
    lender: str | None
    currency: str
    installment_count: int
    first_due_date: date
    # purchase
    item_price: str | None = None
    down_payment: str | None = None
    category_id: uuid.UUID | None = None
    # purchase and loan: where the money went (or came from)
    account_id: uuid.UUID | None = None
    start_date: date | None = None
    # loan and existing: the principal (for existing: what still remains)
    principal: str | None = None
    # existing: installments already paid before FinPilot
    paid_before_count: int = 0
    # As the agreement states them, per installment. Never calculated.
    interest_per_installment: str | None = None
    fee_per_installment: str | None = None
    notes: str | None = None
    goal_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Schedule:
    due_dates: list[date]
    principal: list[int]
    interest: int
    fee: int

    @property
    def per_installment(self) -> list[int]:
        return [p + self.interest + self.fee for p in self.principal]


def schedule_for(
    principal_minor: int, count: int, first_due: date, interest: int, fee: int
) -> Schedule:
    day = first_due.day
    return Schedule(
        due_dates=[add_months(first_due, k, day) for k in range(count)],
        principal=split(principal_minor, count),
        interest=interest,
        fee=fee,
    )


def _check_draft(db: Session, user: User, draft: PlanDraft) -> tuple[int, int, int, int, int]:
    """(principal, price, down payment, interest, fee), all checked."""
    if draft.origin not in ORIGINS:
        raise ApiError(422, "validation_error", "Unknown origin")
    money.currency(db, draft.currency)
    if not 1 <= draft.installment_count <= 600:
        raise ApiError(422, "validation_error", "Between 1 and 600 installments")
    price = down = 0
    if draft.origin == "purchase":
        if not draft.item_price:
            raise ApiError(422, "price_required", "Enter what it cost")
        price = _positive(db, draft.item_price, draft.currency)
        down = _optional(db, draft.down_payment, draft.currency)
        if down >= price:
            raise ApiError(
                422, "down_payment_too_large", "The down payment must be less than the price"
            )
        principal = price - down
    else:
        if not draft.principal:
            raise ApiError(422, "principal_required", "Enter the amount")
        principal = _positive(db, draft.principal, draft.currency)
    paid_before = draft.paid_before_count if draft.origin == "existing" else 0
    if not 0 <= paid_before < draft.installment_count:
        raise ApiError(
            422, "validation_error", "Installments already paid must be fewer than the total"
        )
    if principal < draft.installment_count - paid_before:
        raise ApiError(422, "principal_too_small", "The amount is too small to split that way")
    interest = _optional(db, draft.interest_per_installment, draft.currency)
    fee = _optional(db, draft.fee_per_installment, draft.currency)
    return principal, price, down, interest, fee


def preview(db: Session, user: User, draft: PlanDraft) -> Schedule:
    principal, _, _, interest, fee = _check_draft(db, user, draft)
    count = draft.installment_count - (draft.paid_before_count if draft.origin == "existing" else 0)
    return schedule_for(principal, count, draft.first_due_date, interest, fee)


def _interest_category(db: Session, user: User) -> Category:
    categories.seed_defaults(db, user.id)  # adds "Interest & fees" for people who predate it
    found = db.scalar(
        select(Category).where(Category.user_id == user.id, Category.system_key == INTEREST_KEY)
    )
    if found is None:  # pragma: no cover - seeded just above
        raise ApiError(500, "internal_error", "Category missing")
    return found


def _check_goal(db: Session, user: User, goal_id: uuid.UUID | None, currency: str) -> None:
    if goal_id is None:
        return
    goal = db.scalar(
        select(SavingsGoal).where(SavingsGoal.id == goal_id, SavingsGoal.user_id == user.id)
    )
    if goal is None:
        raise not_found()
    if goal.currency != currency:
        raise ApiError(422, "goal_currency_mismatch", "The goal must be in the plan's currency")
    existing = for_goal(db, goal_id)
    if existing is not None:
        raise ApiError(409, "goal_already_linked", "That goal already follows another plan")


def create(db: Session, user: User, draft: PlanDraft) -> InstallmentPlan:
    count = int(db.scalar(select(func.count()).where(InstallmentPlan.user_id == user.id)) or 0)
    if count >= MAX_PLANS:
        raise ApiError(409, "too_many_plans", "You have the most installment plans allowed")
    principal, price, down, interest, fee = _check_draft(db, user, draft)
    _check_goal(db, user, draft.goal_id, draft.currency)
    name = clean_text(draft.name, max_length=80)
    lender = (
        clean_text(draft.lender, max_length=80) if draft.lender and draft.lender.strip() else None
    )
    exponent = money.currency(db, draft.currency).exponent
    purchase: Transaction | None = None

    if draft.origin in ("purchase", "loan"):
        if draft.account_id is None:
            raise ApiError(422, "account_required", "Choose the account")
        account: FinancialAccount = lock_accounts(db, user, [draft.account_id])[draft.account_id]
        if account.currency != draft.currency:
            raise ApiError(
                422,
                "account_currency_mismatch",
                "Choose an account in the plan's currency",
                {"account_currency": account.currency},
            )
    if draft.origin == "purchase":
        if draft.category_id is None:
            raise ApiError(422, "category_required", "Choose a category for the purchase")
        # The full price, once, as spending (owner decision 2026-11-14).
        purchase = transactions.create(
            db,
            user,
            transactions.Draft(
                type="expense",
                account_id=draft.account_id,  # type: ignore[arg-type]
                amount=money.to_amount(price, exponent),
                currency=draft.currency,
                category_id=draft.category_id,
                effective_date=draft.start_date,
                description=name,
                notes=None,
            ),
            via="installment",
        ).transaction
    debt, _ = debts.create(
        db,
        user,
        debts.DebtDraft(
            direction="i_owe",
            counterparty=lender or name,
            currency=draft.currency,
            amount=money.to_amount(principal, exponent),
            start_date=draft.start_date,
            notes=None,
            # Purchase: the financed part arrives in the same account the price left, so the
            # account loses only the down payment. Loan: the money arrived there. Existing: none.
            account_id=draft.account_id if draft.origin != "existing" else None,
            account_amount=None,
        ),
    )
    plan = InstallmentPlan(
        user_id=user.id,
        debt_id=debt.id,
        name=name,
        lender=lender,
        currency=draft.currency,
        item_price_minor=price or None,
        down_payment_minor=down if draft.origin == "purchase" else None,
        purchase_transaction_id=purchase.id if purchase else None,
        installment_count=draft.installment_count,
        paid_before_count=draft.paid_before_count if draft.origin == "existing" else 0,
        interest_mode="fixed" if interest or fee else "none",
        goal_id=draft.goal_id,
        notes=draft.notes.strip()[:500] if draft.notes and draft.notes.strip() else None,
    )
    db.add(plan)
    db.flush()
    schedule = schedule_for(
        principal,
        draft.installment_count - plan.paid_before_count,
        draft.first_due_date,
        interest,
        fee,
    )
    _write_items(db, plan, plan.paid_before_count + 1, schedule)
    if interest or fee:
        _interest_category(db, user)
    audit(db, "installment.created", user.id, entity_type="installment_plan", entity_id=plan.id)
    return plan


def _write_items(db: Session, plan: InstallmentPlan, first_sequence: int, s: Schedule) -> None:
    for k, (due, principal) in enumerate(zip(s.due_dates, s.principal, strict=True)):
        db.add(
            InstallmentItem(
                user_id=plan.user_id,
                plan_id=plan.id,
                sequence=first_sequence + k,
                due_date=due,
                principal_minor=principal,
                interest_minor=s.interest,
                fee_minor=s.fee,
            )
        )
    db.flush()


# --- Paying


@dataclass(frozen=True)
class PaymentDraft:
    account_id: uuid.UUID
    principal: str | None  # in the plan's currency
    principal_account_amount: str | None  # in the account's currency, when it differs
    interest: str | None
    fee: str | None
    paid_on: date | None
    note: str | None


def pay(db: Session, user: User, plan_id: uuid.UUID, draft: PaymentDraft) -> InstallmentPayment:
    """Confirms a real payment. The installment counts as paid only because of this."""
    plan = owned(db, user, plan_id, lock=True)
    if plan.status != "active":
        raise ApiError(409, "plan_archived", "This plan is archived")
    exponent = money.currency(db, plan.currency, for_new_records=False).exponent
    principal = _optional(db, draft.principal, plan.currency)
    interest = _optional(db, draft.interest, plan.currency)
    fee = _optional(db, draft.fee, plan.currency)
    if principal + interest + fee == 0:
        raise ApiError(422, "amount_not_positive", "Enter what was paid")
    account = lock_accounts(db, user, [draft.account_id])[draft.account_id]
    if (interest or fee) and account.currency != plan.currency:
        raise ApiError(
            422,
            "interest_account_currency",
            "Interest and fees are paid in the plan's currency: choose an account in it, or "
            "record them separately",
            {"plan_currency": plan.currency},
        )
    description = (
        clean_text(draft.note, max_length=100) if draft.note and draft.note.strip() else plan.name
    )
    principal_tx = interest_tx = fee_tx = None
    if principal:
        principal_tx = debts.pay(
            db,
            user,
            plan.debt_id,
            debts.Payment(
                account_id=draft.account_id,
                amount=money.to_amount(principal, exponent),
                account_amount=draft.principal_account_amount,
                effective_date=draft.paid_on,
                description=description,
            ),
            via="installment",
        )
    category = _interest_category(db, user) if interest or fee else None
    for part, minor in (("interest", interest), ("fee", fee)):
        if not minor or category is None:
            continue
        recorded = transactions.create(
            db,
            user,
            transactions.Draft(
                type="expense",
                account_id=draft.account_id,
                amount=money.to_amount(minor, exponent),
                currency=plan.currency,
                category_id=category.id,
                effective_date=draft.paid_on,
                description=description,
                notes=part,
            ),
            via="installment",
        ).transaction
        if part == "interest":
            interest_tx = recorded
        else:
            fee_tx = recorded
    paid_on = next(t.effective_date for t in (principal_tx, interest_tx, fee_tx) if t is not None)
    row = InstallmentPayment(
        user_id=user.id,
        plan_id=plan.id,
        paid_on=paid_on,
        principal_transaction_id=principal_tx.id if principal_tx else None,
        interest_transaction_id=interest_tx.id if interest_tx else None,
        fee_transaction_id=fee_tx.id if fee_tx else None,
    )
    db.add(row)
    plan.version += 1
    db.flush()
    audit(db, "installment.paid", user.id, entity_type="installment_plan", entity_id=plan.id)
    return row


# --- Changing


@dataclass(frozen=True)
class RescheduleDraft:
    remaining_count: int
    first_due_date: date
    interest_per_installment: str | None
    fee_per_installment: str | None


def reschedule(
    db: Session, user: User, plan_id: uuid.UUID, version: int, draft: RescheduleDraft
) -> InstallmentPlan:
    """New dates or amounts for what isn't paid yet. The old rows stay, marked superseded."""
    plan = owned(db, user, plan_id, lock=True)
    _check_version(plan, version)
    if plan.status != "active":
        raise ApiError(409, "plan_archived", "This plan is archived")
    current = view(db, user, plan)
    if current.settled:
        raise ApiError(409, "plan_settled", "Nothing remains on this plan")
    if not 1 <= draft.remaining_count <= 600:
        raise ApiError(422, "validation_error", "Between 1 and 600 installments")
    open_items = current.open_items
    to_split = sum(i.item.principal_minor for i in open_items)
    if to_split < draft.remaining_count:
        raise ApiError(422, "principal_too_small", "The amount is too small to split that way")
    interest = _optional(db, draft.interest_per_installment, plan.currency)
    fee = _optional(db, draft.fee_per_installment, plan.currency)
    now = datetime.now(UTC)
    for state in open_items:
        state.item.superseded_at = now
    db.flush()
    first_sequence = (
        max(
            (i.item.sequence for i in current.items if i.state == "paid"),
            default=plan.paid_before_count,
        )
        + 1
    )
    _write_items(
        db,
        plan,
        first_sequence,
        schedule_for(to_split, draft.remaining_count, draft.first_due_date, interest, fee),
    )
    plan.installment_count = first_sequence - 1 + draft.remaining_count
    if interest or fee:
        plan.interest_mode = "fixed"
        _interest_category(db, user)
    plan.version += 1
    db.flush()
    audit(db, "installment.rescheduled", user.id, entity_type="installment_plan",
          entity_id=plan.id, superseded=str(len(open_items)))  # fmt: skip
    return plan


def _check_version(plan: InstallmentPlan, version: int | None) -> None:
    if version is not None and plan.version != version:
        raise ApiError(
            412, "version_conflict", "This plan changed; reload it", {"version": plan.version}
        )


def update(
    db: Session,
    user: User,
    plan_id: uuid.UUID,
    version: int,
    changes: dict[str, object],
) -> InstallmentPlan:
    """Name, lender, notes and the linked goal. Money and schedule change only through their own
    actions (a payment, a reschedule)."""
    plan = owned(db, user, plan_id, lock=True)
    _check_version(plan, version)
    if changes.get("name"):
        plan.name = clean_text(str(changes["name"]), max_length=80)
    if "lender" in changes:
        value = changes["lender"]
        plan.lender = (
            clean_text(str(value), max_length=80) if value and str(value).strip() else None
        )
    if "notes" in changes:
        value = changes["notes"]
        plan.notes = str(value).strip()[:500] if value and str(value).strip() else None
    if "goal_id" in changes:
        goal_id = changes["goal_id"]
        if goal_id != plan.goal_id:
            if goal_id is not None:
                _check_goal(db, user, goal_id, plan.currency)  # type: ignore[arg-type]
            plan.goal_id = goal_id  # type: ignore[assignment]
    plan.version += 1
    db.flush()
    audit(db, "installment.updated", user.id, entity_type="installment_plan", entity_id=plan.id)
    return plan


def archive(
    db: Session, user: User, plan_id: uuid.UUID, version: int, archived: bool
) -> InstallmentPlan:
    """Hides a settled plan; its history stays. Only a settled plan can be archived."""
    plan = owned(db, user, plan_id, lock=True)
    _check_version(plan, version)
    debt = debts.owned(db, user, plan.debt_id, lock=True)
    if archived and debts.remaining(db, debt) != 0:
        raise ApiError(
            409,
            "plan_not_settled",
            "Only a fully paid plan can be archived. Remove it instead "
            "if it was recorded by mistake",
        )
    plan.status = "archived" if archived else "active"
    plan.version += 1
    # The debt follows its plan (it's at zero when archived, as debts.archive requires).
    if debt.status != plan.status:
        debt.status = plan.status
        debt.version += 1
    db.flush()
    audit(db, f"installment.{'archived' if archived else 'restored'}", user.id,
          entity_type="installment_plan", entity_id=plan.id)  # fmt: skip
    return plan


def remove(db: Session, user: User, plan_id: uuid.UUID, version: int) -> InstallmentPlan:
    """For a plan recorded by mistake, before any payment: voids its purchase and debt start (the
    documents and their reversals stay, as for any void) and archives it. Never deletes money."""
    plan = owned(db, user, plan_id, lock=True)
    _check_version(plan, version)
    live_payments = [
        p
        for p in db.scalars(select(InstallmentPayment).where(InstallmentPayment.plan_id == plan.id))
        if any(
            (t := db.get(Transaction, i)) is not None and t.status == "posted"
            for i in (p.principal_transaction_id, p.interest_transaction_id, p.fee_transaction_id)
            if i is not None
        )
    ]
    if live_payments:
        raise ApiError(
            409, "plan_has_payments", "This plan has payments. Void them first, or keep the plan"
        )
    start = db.scalar(
        select(Transaction).where(
            Transaction.debt_id == plan.debt_id,
            Transaction.type == "debt_start",
            Transaction.status == "posted",
        )
    )
    if start is not None:
        transactions.void(db, user, start.id, start.version, by_plan=True)
    if plan.purchase_transaction_id is not None:
        purchase = db.get(Transaction, plan.purchase_transaction_id)
        if purchase is not None and purchase.status == "posted":
            transactions.void(db, user, purchase.id, purchase.version, by_plan=True)
    debt = debts.owned(db, user, plan.debt_id, lock=True)
    debt.status = "archived"
    debt.version += 1
    plan.status = "archived"
    plan.goal_id = None
    plan.version += 1
    db.flush()
    audit(db, "installment.removed", user.id, entity_type="installment_plan", entity_id=plan.id)
    return plan


def plan_of_transaction(db: Session, document: Transaction) -> InstallmentPlan | None:
    """The plan a purchase or debt start belongs to, if any (they're voided only with the plan)."""
    if document.type == "expense":
        return db.scalar(
            select(InstallmentPlan).where(InstallmentPlan.purchase_transaction_id == document.id)
        )
    if document.type == "debt_start" and document.debt_id is not None:
        return db.scalar(select(InstallmentPlan).where(InstallmentPlan.debt_id == document.debt_id))
    return None


# --- Analytics (§16.10): expected obligations only. Nothing here is spending or posts anything.


WINDOWS = (1, 3, 6, 12)


@dataclass
class CurrencyOverview:
    currency: str
    monthly_minor: int = 0  # each active plan's next installment, summed
    remaining_principal_minor: int = 0
    scheduled_interest_minor: int = 0  # interest and fees still on the schedule
    remaining_count: int = 0
    overdue_count: int = 0
    overdue_minor: int = 0
    due_within: dict[int, int] = field(default_factory=lambda: dict.fromkeys(WINDOWS, 0))
    # Average monthly income over the last three full months, in this currency (from the ledger).
    average_income_minor: int = 0
    income_months: int = 0
    last_payoff: date | None = None


@dataclass(frozen=True)
class UpcomingInstallment:
    plan: InstallmentPlan
    state: ItemState
    amount_minor: int  # what's still expected for it (principal outstanding + interest + fee)


def _expected(state: ItemState) -> int:
    return state.outstanding_principal + state.item.interest_minor + state.item.fee_minor


def overview(
    db: Session, user: User, views: list[PlanView]
) -> tuple[list[CurrencyOverview], list[UpcomingInstallment]]:
    """Per currency (never converted): what's committed, what's due when, and what's overdue."""
    from app.services import summary  # reporting reads installments only here

    today = today_for(user)
    out: dict[str, CurrencyOverview] = {}
    upcoming: list[UpcomingInstallment] = []
    horizon = {w: add_months(today, w, today.day) for w in WINDOWS}
    for v in views:
        if v.plan.status != "active":
            continue
        c = out.setdefault(v.plan.currency, CurrencyOverview(v.plan.currency))
        c.remaining_principal_minor += v.remaining_minor
        c.scheduled_interest_minor += v.scheduled_interest_left
        c.remaining_count += v.remaining_count
        if v.next_item is not None:
            c.monthly_minor += v.next_item.total_minor
        for state in v.open_items:
            amount = _expected(state)
            if state.state == "overdue":
                c.overdue_count += 1
                c.overdue_minor += amount
            for w, end in horizon.items():
                if state.item.due_date < end:
                    c.due_within[w] += amount
            if state.item.due_date < horizon[12]:
                upcoming.append(UpcomingInstallment(v.plan, state, amount))
        completion = v.completion_date
        if completion and (c.last_payoff is None or completion > c.last_payoff):
            c.last_payoff = completion
    # Income: the last three complete months, per currency, as recorded.
    first_of_month = today.replace(day=1)
    for k in (1, 2, 3):
        month = add_months(first_of_month, -k, 1)
        for row in summary.month_summary(db, user, month):
            if row.currency in out and row.income_minor:
                out[row.currency].average_income_minor += row.income_minor
                out[row.currency].income_months += 1
    for c in out.values():
        if c.income_months:
            # Over the months that had income recorded; the count is reported with it.
            c.average_income_minor //= c.income_months
    upcoming.sort(key=lambda u: (u.state.item.due_date, u.plan.name))
    return sorted(out.values(), key=lambda c: c.currency), upcoming
