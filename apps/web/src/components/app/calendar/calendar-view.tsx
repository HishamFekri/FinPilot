"use client";

import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { type CalendarMonth, calendarApi } from "@/lib/everyday";
import { type Transaction, financeApi } from "@/lib/finance";
import { localeTag } from "@/lib/format";
import { formatMoney, isZero } from "@/lib/money";
import type { Occurrence } from "@/lib/recurring";
import { cn } from "@/lib/utils";
import { todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import fin from "../finance/finance.module.css";
import { TransactionRow, useAccountName, useFinanceError } from "../finance/parts";
import { TransactionDialog, useTxnDialog } from "../finance/transaction-dialog";
import { ConfirmDialog, OccurrenceRow } from "../recurring/parts";
import { Button, Loading, Notice, Tag } from "../ui";
import { Link } from "@/i18n/navigation";
import s from "./calendar.module.css";
import { Select } from "../select";

/**
 * A month of money, one currency at a time (never added across currencies). Days are the
 * person's own calendar days. Expected recurring payments are shown as expected until the person
 * confirms them; nothing is marked paid here.
 */
export function CalendarView() {
  return (
    <FinanceGate>
      <CalendarPage />
    </FinanceGate>
  );
}

function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const d = new Date(Date.UTC(y, m - 1 + by, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function CalendarPage() {
  const t = useTranslations("app.calendar");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, revision, quickAdd } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const today = todayIn(user.time_zone);
  const [month, setMonth] = useState(today.slice(0, 7));
  const [currency, setCurrency] = useState("");
  const [accountId, setAccountId] = useState("");
  const [selected, setSelected] = useState<string>(today);
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{ for: string; value?: CalendarMonth; failed?: boolean } | null>(
    null,
  );
  const [confirm, setConfirm] = useState<{ n: number; occurrence: Occurrence } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const grid = useRef<HTMLDivElement>(null);
  const request = JSON.stringify([month, currency, accountId, revision, attempt]);

  useEffect(() => {
    let cancelled = false;
    calendarApi
      .month({ month, currency: currency || undefined, account_id: accountId || undefined })
      .then((value) => {
        if (!cancelled) setData({ for: request, value });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setData({ for: request, failed: true });
      });
    return () => {
      cancelled = true;
    };
    // `request` captures every input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, handleAuthError]);

  const value = data?.value;
  const stale = data !== null && data.for !== request;
  const tag = localeTag(locale);
  const monthLabel = new Intl.DateTimeFormat(tag, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${month}-15T12:00:00Z`));
  const weekdays = Array.from({ length: 7 }, (_, i) =>
    new Intl.DateTimeFormat(tag, { weekday: "short", timeZone: "UTC" }).format(
      new Date(Date.UTC(2024, 0, 1 + i)), // 1 Jan 2024 was a Monday
    ),
  );

  const goTo = (next: string) => {
    setMonth(next.slice(0, 7));
    setSelected(next);
  };

  // Arrow keys move between days, mirrored in right-to-left layouts.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, number> = {
      ArrowRight: 1,
      ArrowLeft: -1,
      ArrowDown: 7,
      ArrowUp: -7,
    };
    let step = moves[event.key];
    if (step === undefined) return;
    if (document.documentElement.dir === "rtl" && Math.abs(step) === 1) step = -step;
    event.preventDefault();
    const d = new Date(`${selected}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + step);
    const next = d.toISOString().slice(0, 10);
    goTo(next);
    window.requestAnimationFrame(() =>
      grid.current?.querySelector<HTMLButtonElement>(`[data-day="${next}"]`)?.focus(),
    );
  };

  const days = value?.days ?? [];
  const first = days[0]?.day;
  const lead = first ? (new Date(`${first}T12:00:00Z`).getUTCDay() + 6) % 7 : 0;
  const peak = Math.max(0, ...days.map((d) => Math.max(Number(d.expenses.amount), 0)));
  const occurrencesOn = (day: string) =>
    (value?.occurrences ?? []).filter((o) => o.window_end === day);
  const installmentsOn = (day: string) =>
    (value?.installments ?? []).filter((i) => i.due_date === day);
  const selectedDay = days.find((d) => d.day === selected);

  return (
    <div className={s.page}>
      <div className={s.head}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
      </div>

      <div className={s.toolbar}>
        <div className={s.monthNav}>
          <button
            type="button"
            className={s.navButton}
            aria-label={t("previous")}
            onClick={() => goTo(`${shiftMonth(month, -1)}-01`)}
          >
            <ChevronLeft aria-hidden="true" className={fin.flipRtl} />
          </button>
          <h2 className={s.monthLabel} aria-live="polite">
            {monthLabel}
          </h2>
          <button
            type="button"
            className={s.navButton}
            aria-label={t("next")}
            onClick={() => goTo(`${shiftMonth(month, 1)}-01`)}
          >
            <ChevronRight aria-hidden="true" className={fin.flipRtl} />
          </button>
          <Button size="sm" variant="ghost" onClick={() => goTo(today)}>
            {t("today")}
          </Button>
        </div>
        <div className={s.filters}>
          {value && value.currencies.length > 1 ? (
            <div className={s.segmented} role="group" aria-label={t("currency")}>
              {value.currencies.map((code) => (
                <button
                  key={code}
                  type="button"
                  aria-pressed={code === value.currency}
                  onClick={() => setCurrency(code)}
                >
                  <bdi dir="ltr">{code}</bdi>
                </button>
              ))}
            </div>
          ) : null}
          <Select aria-label={t("account")} value={accountId} onValueChange={setAccountId}>
            <option value="">{t("allAccounts")}</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {`${accountName(a)} · ${a.currency}`}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {data?.failed ? (
        <Notice tone="error">
          {message(null)}{" "}
          <button
            type="button"
            className={appStyles.linkBtn}
            onClick={() => setAttempt((n) => n + 1)}
          >
            {t("retry")}
          </button>
        </Notice>
      ) : !value ? (
        <Loading />
      ) : (
        <div className={cn(s.layout, stale && fin.refreshing)} aria-busy={stale || undefined}>
          <div className={s.calendar}>
            {value.currency ? null : <p className={s.note}>{t("empty")}</p>}
            <div className={s.weekdays} aria-hidden="true">
              {weekdays.map((w) => (
                <span key={w}>{w}</span>
              ))}
            </div>
            <div
              ref={grid}
              className={s.grid}
              role="grid"
              aria-label={t("gridLabel", { month: monthLabel })}
              onKeyDown={onKeyDown}
            >
              {Array.from({ length: lead }, (_, i) => (
                <span key={`lead-${i}`} className={s.blank} aria-hidden="true" />
              ))}
              {days.map((d) => {
                const spent = Number(d.expenses.amount);
                const level = spent > 0 && peak > 0 ? Math.ceil((spent / peak) * 4) : 0;
                const due = [...occurrencesOn(d.day), ...installmentsOn(d.day)];
                const dayNumber = Number(d.day.slice(8));
                const label = [
                  new Intl.DateTimeFormat(tag, {
                    weekday: "long",
                    day: "numeric",
                    month: "long",
                    timeZone: "UTC",
                  }).format(new Date(`${d.day}T12:00:00Z`)),
                  !isZero(d.expenses)
                    ? t("spent", { amount: formatMoney(d.expenses, locale) })
                    : "",
                  !isZero(d.income) ? t("received", { amount: formatMoney(d.income, locale) }) : "",
                  d.transactions ? t("count", { count: d.transactions }) : "",
                  due.length ? t("due", { count: due.length }) : "",
                ]
                  .filter(Boolean)
                  .join(", ");
                return (
                  <button
                    key={d.day}
                    type="button"
                    role="gridcell"
                    data-day={d.day}
                    className={s.cell}
                    data-level={level || undefined}
                    data-today={d.day === value.today || undefined}
                    aria-selected={d.day === selected}
                    aria-label={label}
                    tabIndex={d.day === selected ? 0 : -1}
                    onClick={() => setSelected(d.day)}
                  >
                    <span className={s.dayNumber}>{dayNumber}</span>
                    {!isZero(d.expenses) ? (
                      <bdi dir="ltr" className={s.spent}>
                        {formatMoney(d.expenses, locale, { display: "none" })}
                      </bdi>
                    ) : null}
                    {!isZero(d.income) ? (
                      <bdi dir="ltr" className={s.income}>
                        {`+${formatMoney(d.income, locale, { display: "none" })}`}
                      </bdi>
                    ) : null}
                    <span className={s.marks} aria-hidden="true">
                      {due.slice(0, 3).map((o) => (
                        <span
                          key={"id" in o ? o.id : `${o.plan_id}-${o.sequence}`}
                          className={s.mark}
                          data-state={o.state}
                        />
                      ))}
                      {d.transactions ? (
                        <span className={s.countBadge}>{d.transactions}</span>
                      ) : null}
                    </span>
                  </button>
                );
              })}
            </div>
            <ul className={s.legend}>
              <li>
                <span className={s.mark} data-state="confirmed" /> {t("legendConfirmed")}
              </li>
              <li>
                <span className={s.mark} data-state="expected" /> {t("legendExpected")}
              </li>
              <li>
                <span className={s.mark} data-state="overdue" /> {t("legendOverdue")}
              </li>
            </ul>
            {value.currency ? (
              <p className={s.note}>{t("currencyNote", { currency: value.currency })}</p>
            ) : null}
          </div>

          <DayPanel
            day={selected}
            today={value.today}
            currency={value.currency}
            accountId={accountId}
            summary={selectedDay ?? null}
            occurrences={occurrencesOn(selected)}
            installments={installmentsOn(selected)}
            revision={revision}
            onAdd={() =>
              quickAdd.openWith({
                n: Date.now(),
                date: selected,
                accountId: accountId || null,
              })
            }
            onConfirm={(o) => {
              setConfirm((p) => ({ n: (p?.n ?? 0) + 1, occurrence: o }));
              setConfirmOpen(true);
            }}
            onAnswered={() => setAttempt((n) => n + 1)}
          />
        </div>
      )}

      {confirm ? (
        <ConfirmDialog
          key={confirm.n}
          occurrence={confirm.occurrence}
          open={confirmOpen}
          onClose={() => setConfirmOpen(false)}
          onDone={() => {
            setConfirmOpen(false);
            setAttempt((n) => n + 1);
          }}
        />
      ) : null}
    </div>
  );
}

function DayPanel({
  day,
  today,
  currency,
  accountId,
  summary,
  occurrences,
  installments = [],
  revision,
  onAdd,
  onConfirm,
  onAnswered,
}: {
  day: string;
  today: string;
  currency: string | null;
  accountId: string;
  summary: CalendarMonth["days"][number] | null;
  occurrences: Occurrence[];
  installments?: NonNullable<CalendarMonth["installments"]>;
  revision: number;
  onAdd: () => void;
  onConfirm: (o: Occurrence) => void;
  onAnswered: () => void;
}) {
  const t = useTranslations("app.calendar");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const dialog = useTxnDialog();
  const [rows, setRows] = useState<{ for: string; items?: Transaction[] } | null>(null);
  const request = JSON.stringify([day, currency, accountId, revision]);

  useEffect(() => {
    let cancelled = false;
    financeApi
      .history(
        { from: day, to: day, currency: currency ?? undefined, account_id: accountId || undefined },
        null,
        50,
      )
      .then((page) => {
        if (!cancelled) setRows({ for: request, items: page.items });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setRows({ for: request, items: [] });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, handleAuthError]);

  const title = new Intl.DateTimeFormat(localeTag(locale), {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  }).format(new Date(`${day}T12:00:00Z`));

  return (
    <section className={s.dayPanel} aria-labelledby="day-title">
      <div className={s.dayHead}>
        <h2 id="day-title" className={s.dayTitle}>
          {title}
        </h2>
        <Button size="sm" variant="primary" onClick={onAdd} disabled={day > today}>
          <Plus aria-hidden="true" />
          {t("add")}
        </Button>
      </div>
      {summary && currency ? (
        <dl className={s.dayFigures}>
          <div>
            <dt>{t("spentLabel")}</dt>
            <dd>
              <bdi dir="ltr">{formatMoney(summary.expenses, locale)}</bdi>
            </dd>
          </div>
          <div>
            <dt>{t("receivedLabel")}</dt>
            <dd className={fin.in}>
              <bdi dir="ltr">{formatMoney(summary.income, locale)}</bdi>
            </dd>
          </div>
          {summary.trip_shares ? (
            <div>
              <dt>{t("tripShares")}</dt>
              <dd>
                <bdi dir="ltr">{formatMoney(summary.trip_shares, locale)}</bdi>
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}
      {day > today ? <p className={s.note}>{t("future")}</p> : null}
      {occurrences.length > 0 ? (
        <>
          <h3 className={s.subhead}>{t("expected")}</h3>
          <ul className={s.occurrences}>
            {occurrences.map((o) => (
              <OccurrenceRow
                key={o.id}
                occurrence={o}
                onConfirm={onConfirm}
                onAnswered={onAnswered}
                compact
              />
            ))}
          </ul>
        </>
      ) : null}
      {installments.length > 0 ? (
        <>
          <h3 className={s.subhead}>{t("installments")}</h3>
          <ul className={cn(fin.rows, fin.rowsWrap)}>
            {installments.map((i) => (
              <li key={`${i.plan_id}-${i.sequence}`} className={s.installmentRow}>
                <Link href={`/app/installments/${i.plan_id}`} dir="auto">
                  {t("installmentLine", { name: i.plan_name, sequence: i.sequence })}
                </Link>
                <span>{formatMoney(i.amount, locale)}</span>
                {i.state === "overdue" ? <Tag tone="accent">{t("overdue")}</Tag> : null}
              </li>
            ))}
          </ul>
          <p className={s.note}>{t("installmentsNote")}</p>
        </>
      ) : null}
      <h3 className={s.subhead}>{t("entries")}</h3>
      {!rows || rows.for !== request ? (
        <Loading />
      ) : rows.items && rows.items.length > 0 ? (
        <ul className={cn(fin.rows, fin.rowsWrap)}>
          {rows.items.map((txn) => (
            <li key={txn.id}>
              <TransactionRow txn={txn} onOpen={(item) => dialog.show(item.id)} />
            </li>
          ))}
        </ul>
      ) : (
        <p className={s.note}>{t("noEntries")}</p>
      )}
      <TransactionDialog dialog={dialog} />
    </section>
  );
}
