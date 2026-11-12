"""The trip report: summary, CSVs and PDF (API_SPEC.md §10, FINANCIAL_RULES.md §16.6).

Trip figures only, for any current or former member (a former member sees their own part, as on
the trip's pages; never-a-member is 404). Built by `services.trip_reports`, which can't import
personal finance, so no account, balance, transaction or link of anyone can appear here. Read in
one REPEATABLE READ transaction so the summary, tables and files agree. Exports are rate limited
and reuse the report export's CSV safety, PDF sandbox, limits and completeness disclosure.
Print uses the same PDF.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import rate_limited, require_active_user
from app.api.v1.accounts import MoneyOut, _no_store
from app.api.v1.trips import Formatter, Person
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import Limit
from app.db import rls
from app.db import session as db_session
from app.db.models import TRIP_CATEGORY_KEYS, FxRate, User
from app.services import exports, trip_reports, trips
from app.services.report_text import text

DASH = " – "  # noqa: RUF001 - an en dash between dates
router = APIRouter(tags=["trip-reports"], dependencies=[Depends(_no_store)])
export_limit = Depends(rate_limited("trips.report_export", Limit(requests=10, window_seconds=60)))
heavy_read = Depends(rate_limited("analytics.read", Limit(requests=120, window_seconds=60)))
Locale = Literal["en", "ar", "tr"]


@contextmanager
def _snapshot(user: User) -> Iterator[Session]:
    session = db_session._session_factory()()
    try:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(select(1))
        rls.act_as(session, user.id)  # its own session, so it needs the identity too
        yield session
    finally:
        session.close()


def report_filters(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    member_id: uuid.UUID | None = None,
) -> trip_reports.ReportFilters:
    if date_from and date_to and date_from > date_to:
        raise ApiError(422, "validation_error", "The start is after the end")
    if category is not None and category not in TRIP_CATEGORY_KEYS:
        raise ApiError(422, "validation_error", "Unknown category")
    return trip_reports.ReportFilters(date_from, date_to, category, member_id)


# --- Shapes


class FiltersOut(BaseModel):
    date_from: date | None
    date_to: date | None
    category_key: str | None
    member_id: uuid.UUID | None


class CategoryLine(BaseModel):
    category_key: str
    amount: MoneyOut
    percent: str


class MemberLine(Person):
    role: str
    status: str
    paid: MoneyOut  # expenses they paid, within the filters
    benefited: MoneyOut  # their shares, within the filters
    trip_paid: MoneyOut  # the whole trip, for the balance
    trip_share: MoneyOut
    sent: MoneyOut  # confirmed payments
    received: MoneyOut
    pending_sent: MoneyOut  # not counted until confirmed
    pending_received: MoneyOut
    net: MoneyOut  # + is owed, - owes; whole trip, confirmed only


class PaymentLine(BaseModel):
    id: uuid.UUID
    kind: Literal["payment", "reversal"]
    status: str
    payer: Person
    recipient: Person
    amount: MoneyOut  # in the trip's currency, as it counts
    paid: MoneyOut  # what was actually paid (another currency when converted)
    paid_on: date


class SuggestionLine(BaseModel):
    payer: Person
    recipient: Person
    amount: MoneyOut


class ShareLine(BaseModel):
    person: Person
    amount: MoneyOut


class ExpenseLine(BaseModel):
    id: uuid.UUID
    expense_date: date
    description: str
    category_key: str
    payer: Person
    amount: MoneyOut  # as on the receipt
    base_amount: MoneyOut  # in the trip's currency, as counted
    rate: str | None  # the rate the expense was approved with, and where it came from
    rate_source: str | None
    shares: list[ShareLine]


class ReportLimits(BaseModel):
    pdf_rows: int
    csv_rows: int


class TripReportOut(BaseModel):
    trip_id: uuid.UUID
    name: str
    destination: str | None
    start_date: date | None
    end_date: date | None
    base_currency: str
    status: str
    generated_at: datetime
    scope: Literal["full", "mine"]
    filters: FiltersOut
    filtered: bool
    total: MoneyOut
    expense_count: int
    pending_expenses: int  # waiting for approval: never counted
    by_category: list[CategoryLine]
    members: list[MemberLine]
    outstanding: MoneyOut
    balanced: bool
    settlements_confirmed: list[PaymentLine]
    settlements_pending: list[PaymentLine]
    # Separate from payments: recalculated, never stored, never paid.
    suggestions: list[SuggestionLine]
    expenses: list[ExpenseLine]
    limits: ReportLimits


def _percent(part: int, whole: int) -> str:
    if whole <= 0:
        return "0.0"
    return str((Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1")))


def _build(
    db: Session, user: User, trip_id: uuid.UUID, f: trip_reports.ReportFilters, settings: Settings
) -> TripReportOut:
    a = trips.access(db, user, trip_id)
    r = trip_reports.build(db, a, f)
    fmt = Formatter(db, a.trip)
    code = a.trip.base_currency
    rate_ids = {e.version.rate_id for e in r.expenses if e.version.rate_id}
    rates = (
        {x.id: x for x in db.scalars(select(FxRate).where(FxRate.id.in_(rate_ids)))}
        if rate_ids
        else {}
    )

    def m(minor: int) -> MoneyOut:
        return fmt.money(minor, code)

    def payment(v: Any) -> PaymentLine:
        row = v.row
        return PaymentLine(
            id=row.id, kind=row.kind, status=v.status, payer=fmt.person(row.payer_user_id),
            recipient=fmt.person(row.recipient_user_id),
            amount=fmt.money(row.base_amount_minor, code),
            paid=fmt.money(row.amount_minor, row.currency), paid_on=row.paid_on,
        )  # fmt: skip

    def expense(e: Any) -> ExpenseLine:
        v = e.version
        rate = rates.get(v.rate_id) if v.rate_id else None
        return ExpenseLine(
            id=e.expense.id, expense_date=v.expense_date, description=v.description,
            category_key=v.category_key, payer=fmt.person(e.expense.payer_user_id),
            amount=fmt.money(v.amount_minor, v.currency), base_amount=m(v.base_amount_minor),
            rate=format(rate.rate.normalize(), "f") if rate else None,
            rate_source=rate.source if rate else None,
            shares=[
                ShareLine(person=fmt.person(s.user_id), amount=m(s.base_share_minor))
                for s in sorted(e.shares, key=lambda s: fmt.person(s.user_id).display_name)
                if s.base_share_minor
            ],
        )  # fmt: skip

    return TripReportOut(
        trip_id=a.trip.id,
        name=a.trip.name,
        destination=a.trip.destination,
        start_date=a.trip.start_date,
        end_date=a.trip.end_date,
        base_currency=code,
        status=a.trip.status,
        generated_at=datetime.now(UTC),
        scope=r.scope,  # type: ignore[arg-type]
        filters=FiltersOut(
            date_from=f.date_from,
            date_to=f.date_to,
            category_key=f.category_key,
            member_id=f.member_id,
        ),
        filtered=f.active,
        total=m(r.total),
        expense_count=len(r.expenses),
        pending_expenses=r.pending_expenses,
        by_category=[
            CategoryLine(category_key=k, amount=m(v), percent=_percent(v, r.total))
            for k, v in r.by_category.items()
        ],
        members=[
            MemberLine(
                user_id=x.user_id,
                display_name=x.display_name,
                role=x.role,
                status=x.status,
                paid=m(x.paid),
                benefited=m(x.benefited),
                trip_paid=m(x.trip_paid),
                trip_share=m(x.trip_share),
                sent=m(x.sent),
                received=m(x.received),
                pending_sent=m(x.pending_sent),
                pending_received=m(x.pending_received),
                net=m(x.net),
            )
            for x in r.members
        ],
        outstanding=m(r.outstanding),
        balanced=r.balanced,
        settlements_confirmed=[payment(v) for v in r.confirmed],
        settlements_pending=[payment(v) for v in r.pending],
        suggestions=[
            SuggestionLine(payer=fmt.person(p), recipient=fmt.person(q), amount=m(n))
            for p, q, n in r.suggestions
        ],
        expenses=[expense(e) for e in r.expenses],
        limits=ReportLimits(pdf_rows=settings.pdf_max_rows, csv_rows=settings.csv_max_rows),
    )


@router.get("/trips/{trip_id}/reports/summary", dependencies=[heavy_read])
def trip_report(
    trip_id: uuid.UUID,
    request: Request,
    f: trip_reports.ReportFilters = Depends(report_filters),
    user: User = Depends(require_active_user),
) -> TripReportOut:
    with _snapshot(user) as db:
        return _build(db, user, trip_id, f, request.app.state.settings)


# --- CSV


def _csv(name: str, header: list[str], rows: list[list[str]], text_columns: set[int]) -> Response:
    return Response(
        exports.csv_bytes(header, rows, text_columns),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _slug(r: TripReportOut) -> str:
    return f"finpilot-trip-{str(r.trip_id)[:8]}"


@router.get("/trips/{trip_id}/reports/expenses.csv", dependencies=[export_limit])
def trip_expenses_csv(
    trip_id: uuid.UUID,
    request: Request,
    f: trip_reports.ReportFilters = Depends(report_filters),
    locale: Locale = "en",
    user: User = Depends(require_active_user),
) -> Response:
    settings: Settings = request.app.state.settings
    t = text(locale)
    with _snapshot(user) as db:
        r = _build(db, user, trip_id, f, settings)
    if len(r.expenses) > settings.csv_max_rows:
        raise ApiError(413, "export_too_large", "Too many rows for one file; narrow the period",
                       {"limit": settings.csv_max_rows})  # fmt: skip
    header = [
        "date", "description", "category", "paid_by", "amount", "currency", "base_amount",
        "base_currency", "rate", "rate_source", "shares", "expense_id",
    ]  # fmt: skip
    rows = [
        [
            e.expense_date.isoformat(),
            e.description,
            t.get(f"cat_{e.category_key}", ""),
            e.payer.display_name,
            e.amount.amount,
            e.amount.currency,
            e.base_amount.amount,
            e.base_amount.currency,
            e.rate or "",
            e.rate_source or "",
            "; ".join(f"{s.person.display_name} {s.amount.amount}" for s in e.shares),
            str(e.id),
        ]
        for e in r.expenses
    ]
    return _csv(f"{_slug(r)}-expenses.csv", header, rows, {1, 2, 3, 10})


@router.get("/trips/{trip_id}/reports/balances.csv", dependencies=[export_limit])
def trip_balances_csv(
    trip_id: uuid.UUID,
    request: Request,
    f: trip_reports.ReportFilters = Depends(report_filters),
    user: User = Depends(require_active_user),
) -> Response:
    with _snapshot(user) as db:
        r = _build(db, user, trip_id, f, request.app.state.settings)
    header = [
        "member", "role", "status", "paid_in_view", "share_in_view", "paid", "share", "sent",
        "received", "pending_sent", "pending_received", "net", "currency", "user_id",
    ]  # fmt: skip
    rows = [
        [
            x.display_name,
            x.role,
            x.status,
            x.paid.amount,
            x.benefited.amount,
            x.trip_paid.amount,
            x.trip_share.amount,
            x.sent.amount,
            x.received.amount,
            x.pending_sent.amount,
            x.pending_received.amount,
            x.net.amount,
            r.base_currency,
            str(x.user_id),
        ]
        for x in r.members
    ]
    return _csv(f"{_slug(r)}-balances.csv", header, rows, {0})


@router.get("/trips/{trip_id}/reports/settlements.csv", dependencies=[export_limit])
def trip_settlements_csv(
    trip_id: uuid.UUID,
    request: Request,
    f: trip_reports.ReportFilters = Depends(report_filters),
    user: User = Depends(require_active_user),
) -> Response:
    with _snapshot(user) as db:
        r = _build(db, user, trip_id, f, request.app.state.settings)
    header = ["date", "kind", "status", "payer", "recipient", "amount", "currency",
              "paid_amount", "paid_currency", "settlement_id"]  # fmt: skip
    rows = [
        [
            p.paid_on.isoformat(),
            p.kind,
            p.status,
            p.payer.display_name,
            p.recipient.display_name,
            p.amount.amount,
            p.amount.currency,
            p.paid.amount,
            p.paid.currency,
            str(p.id),
        ]
        for p in [*r.settlements_confirmed, *r.settlements_pending]
    ]
    return _csv(f"{_slug(r)}-settlements.csv", header, rows, {3, 4})


# --- PDF (and Print, which prints it)


@router.get("/trips/{trip_id}/reports/export.pdf", dependencies=[export_limit])
def trip_report_pdf(
    trip_id: uuid.UUID,
    request: Request,
    f: trip_reports.ReportFilters = Depends(report_filters),
    locale: Locale = "en",
    user: User = Depends(require_active_user),
) -> Response:
    settings: Settings = request.app.state.settings
    t = text(locale)
    with _snapshot(user) as db:
        r = _build(db, user, trip_id, f, settings)
    cap = settings.pdf_max_rows
    shown = r.expenses[:cap]
    more = len(r.expenses) > cap

    def n(value: MoneyOut) -> str:
        return f"{exports.number(value.amount, locale)} {value.currency}"

    def num(value: int) -> str:
        return exports.number(str(value), locale)

    def who(p: Any, q: Any) -> str:
        return f"{p.display_name} → {q.display_name}"

    top = max((Decimal(c.amount.amount) for c in r.by_category), default=Decimal(0))
    filters = []
    if f.date_from or f.date_to:
        filters.append((t["period"], f"{f.date_from or ''}{DASH}{f.date_to or ''}"))
    if f.category_key:
        filters.append((t["category"], t.get(f"cat_{f.category_key}", f.category_key)))
    if f.member_id:
        name = next((x.display_name for x in r.members if x.user_id == f.member_id), "")
        filters.append((t["members"], name))
    dates = ""
    if r.start_date or r.end_date:
        dates = f"{r.start_date or ''}{DASH}{r.end_date or ''}"
    generated = exports.stamp(r.generated_at.astimezone(_zone(user.time_zone)))
    html = exports.render_html(
        {
            "lang": locale,
            "dir": "rtl" if locale == "ar" else "ltr",
            "t": t,
            "trip": {"name": r.name, "currency": r.base_currency, "dates": dates},
            "generated": generated,
            "filters": filters,
            "filtered": r.filtered,
            "former": t["former_note"] if r.scope == "mine" else "",
            "complete": not more,
            "completeness": (
                t["partial_trip"].format(
                    shown=num(len(shown)),
                    total=num(len(r.expenses)),
                    omitted=num(len(r.expenses) - len(shown)),
                    limit=num(cap),
                )
                if more
                else t["complete_trip"].format(total=num(len(r.expenses)))
            ),
            "total": n(r.total),
            "count": num(r.expense_count),
            "outstanding": n(r.outstanding),
            "member_count": num(sum(1 for x in r.members if x.status == "active")),
            "pending_expenses": t["pending_expenses"].format(n=num(r.pending_expenses))
            if r.pending_expenses
            else "",
            "categories": [
                {
                    "name": t.get(f"cat_{c.category_key}", c.category_key),
                    "amount": n(c.amount),
                    "share": exports.number(c.percent, locale),
                    "width": float(Decimal(c.amount.amount) / top * 100) if top else 0,
                }
                for c in r.by_category
            ],
            "members": [
                {
                    "name": x.display_name,
                    "role": t.get(f"role_{x.role}", x.role),
                    "status": "" if x.status == "active" else t.get(f"status_{x.status}", ""),
                    "paid": n(x.paid),
                    "benefited": n(x.benefited),
                    "sent": n(x.sent),
                    "received": n(x.received),
                    "net": n(x.net),
                    "tone": "pos"
                    if Decimal(x.net.amount) > 0
                    else "neg"
                    if Decimal(x.net.amount) < 0
                    else "",
                    "net_label": t["owed_to"]
                    if Decimal(x.net.amount) > 0
                    else t["owes"]
                    if Decimal(x.net.amount) < 0
                    else t["settled"],
                }
                for x in r.members
            ],
            "confirmed": [
                {
                    "date": p.paid_on.isoformat(),
                    "who": who(p.payer, p.recipient),
                    "amount": n(p.amount),
                    "reversed": p.status == "reversed",
                    "status": t["reversal"]
                    if p.kind == "reversal"
                    else t["reversed"]
                    if p.status == "reversed"
                    else t["state_confirmed"],
                }
                for p in r.settlements_confirmed
            ],
            "pending": [
                {
                    "date": p.paid_on.isoformat(),
                    "who": who(p.payer, p.recipient),
                    "amount": n(p.amount),
                }
                for p in r.settlements_pending
            ],
            "suggestions": [
                {"who": who(s.payer, s.recipient), "amount": n(s.amount)} for s in r.suggestions
            ],
            "rows": [
                {
                    "date": e.expense_date.isoformat(),
                    "description": e.description,
                    "category": t.get(f"cat_{e.category_key}", e.category_key),
                    "payer": e.payer.display_name,
                    "original": n(e.amount) if e.amount.currency != r.base_currency else "",
                    "amount": n(e.base_amount),
                    "shares": ", ".join(s.person.display_name for s in e.shares),
                }
                for e in shown
            ],
        },
        template="trip_report.html",
    )
    pdf = exports.render_pdf(
        html,
        exports.PdfSettings(
            settings.pdf_browser_channel,
            settings.pdf_browser_path,
            settings.pdf_timeout_seconds * 1000,
            settings.pdf_max_concurrent,
        ),
        {
            "dir": "rtl" if locale == "ar" else "ltr",
            "generated": generated,
            "page": t["page"],
            "of": t["of"],
        },
    )
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_slug(r)}-{locale}.pdf"',
            "X-Content-Type-Options": "nosniff",
            "X-Report-Complete": "false" if more else "true",
            "X-Report-Rows-Included": str(len(shown)),
            "X-Report-Rows-Total": str(len(r.expenses)),
        },
    )


def _zone(name: str) -> Any:
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        return UTC
