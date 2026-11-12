"""The trip report (FINANCIAL_RULES.md §16.6): a trip's figures, from trip records only.

A trip module: it never imports personal finance (import-linter), so no account, balance,
transaction, budget or link can appear in it. Members see names, roles and trip figures; never an
email address or anything personal.

- **Counted:** each expense's effective version (approved, not a cancellation). Pending, rejected,
  superseded and cancelled versions are never counted; pending ones are only counted *as a number*
  so the report can say they're left out.
- **Filters** (period by expense date, category, member as payer or beneficiary) narrow the
  expense list and what's derived from it: the total, by category, who paid, who benefited.
  **Balances, settlements, outstanding amounts and suggestions always cover the whole trip**,
  because a balance is only meaningful in full; the report says so.
- **Suggestions are separate**: labelled "Suggested, not paid", recalculated, never stored.
- **A former member** sees only the expenses and payments they are part of, and their own
  balance (SECURITY.md §4.4), the same scope as the trip's own pages.
- Everything is in the trip's base currency's minor units; expenses keep their original amount.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import TripExpenseVersion
from app.services import allocation, settlements, trip_expenses, trips
from app.services.trips import Access


@dataclass(frozen=True)
class ReportFilters:
    date_from: date | None = None
    date_to: date | None = None
    category_key: str | None = None
    member_id: uuid.UUID | None = None

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.date_from, self.date_to, self.category_key,
                                           self.member_id))  # fmt: skip


@dataclass
class MemberLine:
    user_id: uuid.UUID
    display_name: str
    role: str
    status: str
    # The filtered expenses:
    paid: int = 0
    benefited: int = 0
    # The whole trip:
    trip_paid: int = 0
    trip_share: int = 0
    sent: int = 0
    received: int = 0
    pending_sent: int = 0
    pending_received: int = 0

    @property
    def net(self) -> int:
        return self.trip_paid - self.trip_share + self.sent - self.received


@dataclass
class TripReport:
    access: Access
    filters: ReportFilters
    scope: str  # "full", or "mine" for a former member
    expenses: list[trip_expenses.Effective] = field(default_factory=list)
    total: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    members: list[MemberLine] = field(default_factory=list)
    confirmed: list[settlements.SettlementView] = field(default_factory=list)
    pending: list[settlements.SettlementView] = field(default_factory=list)
    suggestions: list[tuple[uuid.UUID, uuid.UUID, int]] = field(default_factory=list)
    pending_expenses: int = 0  # proposed or awaiting approval: not counted

    @property
    def outstanding(self) -> int:
        """What is still owed in total (the sum of positive nets)."""
        return sum(m.net for m in self.members if m.net > 0)

    @property
    def balanced(self) -> bool:
        return self.scope != "full" or sum(m.net for m in self.members) == 0


def _matches(item: trip_expenses.Effective, f: ReportFilters) -> bool:
    v = item.version
    if f.date_from is not None and v.expense_date < f.date_from:
        return False
    if f.date_to is not None and v.expense_date > f.date_to:
        return False
    if f.category_key is not None and v.category_key != f.category_key:
        return False
    if f.member_id is not None:
        involved = {item.expense.payer_user_id} | {s.user_id for s in item.shares}
        if f.member_id not in involved:
            return False
    return True


def build(db: Session, a: Access, f: ReportFilters) -> TripReport:
    me = a.member.user_id
    report = TripReport(a, f, "full" if a.active else "mine")
    lines = {
        m.user_id: MemberLine(m.user_id, m.display_name, m.role, m.status)
        for m in trips.members(db, a.trip.id)
    }

    every = trip_expenses.effective(db, a.trip.id)
    if not a.active:
        every = [
            e for e in every if me in ({e.expense.payer_user_id} | {s.user_id for s in e.shares})
        ]
    # Whole-trip balances: every effective expense and confirmed payment (§9.2).
    for user_id, b in trip_expenses.balances(db, a.trip).items():
        line = lines.get(user_id)
        if line is not None:
            line.trip_paid, line.trip_share = b.paid, b.owed
            line.sent, line.received = b.sent, b.received

    shown = [e for e in every if _matches(e, f)]
    report.expenses = shown
    categories: dict[str, int] = defaultdict(int)
    for item in shown:
        report.total += item.version.base_amount_minor
        categories[item.version.category_key] += item.version.base_amount_minor
        lines[item.expense.payer_user_id].paid += item.version.base_amount_minor
        for share in item.shares:
            lines[share.user_id].benefited += share.base_share_minor
    report.by_category = dict(sorted(categories.items(), key=lambda kv: (-kv[1], kv[0])))

    views = settlements.views(db, a)  # a former member: only their own payments
    for view in views:
        if view.row.state == "confirmed":
            report.confirmed.append(view)
        elif view.row.state == "pending":
            report.pending.append(view)
            if view.row.payer_user_id in lines:
                lines[view.row.payer_user_id].pending_sent += view.row.base_amount_minor
            if view.row.recipient_user_id in lines:
                lines[view.row.recipient_user_id].pending_received += view.row.base_amount_minor

    if a.active:
        report.members = list(lines.values())
        report.suggestions = allocation.suggest({m.user_id: m.net for m in report.members})
        report.pending_expenses = int(
            db.scalar(
                select(func.count(func.distinct(TripExpenseVersion.expense_id))).where(
                    TripExpenseVersion.trip_id == a.trip.id, TripExpenseVersion.state == "pending"
                )
            )
            or 0
        )
    else:
        report.members = [lines[me]] if me in lines else []
        report.suggestions = [
            s for s in allocation.suggest({m.user_id: m.net for m in lines.values()})
            if me in (s[0], s[1])
        ]  # fmt: skip
    return report
