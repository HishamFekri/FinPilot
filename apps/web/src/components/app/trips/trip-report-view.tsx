"use client";

import { ArrowLeft, CircleCheck, Download, FileText, Printer } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { type TripReport, type TripReportFilters, tripReportApi } from "@/lib/links";
import { formatMoney, isNegative, isZero } from "@/lib/money";
import { TRIP_CATEGORIES } from "@/lib/trips";
import { cn } from "@/lib/utils";
import { Money } from "../accounts/parts";
import { useFileExport } from "../analytics/use-file-export";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate } from "../finance/finance-provider";
import fin from "../finance/finance.module.css";
import { Button, Notice, Spinner } from "../ui";
import { isolate, TripCategoryIcon, useDateRange, useTripCategory } from "./parts";
import s from "./trips.module.css";
import { Select } from "../select";

/**
 * The trip report (FINANCIAL_RULES §16.6): trip figures only, the same for every member who sees
 * them. Approved expenses count; pending ones are only mentioned. Filters narrow the expenses;
 * balances, payments and suggestions always cover the whole trip. Suggestions are separate and
 * never paid. CSV, PDF and Print come from the API, with the same limits as Reports.
 */
export function TripReportView({ id }: { id: string }) {
  return (
    <FinanceGate>
      <TripReportPage id={id} />
    </FinanceGate>
  );
}

function TripReportPage({ id }: { id: string }) {
  const t = useTranslations("app.trips.report");
  const reports = useTranslations("app.reports");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const categoryName = useTripCategory();
  const range = useDateRange();
  const formId = useId();
  const exporter = useFileExport({
    complete: (total) => t("pdfComplete", { total }),
    partial: (included, total) => t("pdfPartial", { included, total }),
  });
  const [draft, setDraft] = useState<TripReportFilters>({});
  const [filters, setFilters] = useState<TripReportFilters>({});
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{
    for: string;
    value?: TripReport;
    failed?: boolean;
    missing?: boolean;
  } | null>(null);
  const stamp = `${attempt}:${JSON.stringify(filters)}`;

  useEffect(() => {
    let cancelled = false;
    tripReportApi
      .summary(id, filters)
      .then((value) => {
        if (!cancelled) setData({ for: stamp, value });
      })
      .catch((error: unknown) => {
        if (cancelled || handleAuthError(error)) return;
        setData({
          for: stamp,
          failed: true,
          missing: error instanceof ApiError && error.status === 404,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [id, filters, stamp, handleAuthError]);

  const loading = data?.for !== stamp;
  const value = data?.value;
  const back = (
    <Link href={`/app/trips/${id}`} className={cn(s.back, s.noPrint)}>
      <ArrowLeft aria-hidden="true" className={fin.flipRtl} /> {t("back")}
    </Link>
  );

  if (data?.failed && !value) {
    return (
      <div className={s.page}>
        <div>{back}</div>
        <Notice tone="error">
          {data.missing ? t("notFound") : t("loadFailed")}{" "}
          {data.missing ? null : (
            <button
              type="button"
              className={appStyles.linkBtn}
              onClick={() => setAttempt((n) => n + 1)}
            >
              {t("retry")}
            </button>
          )}
        </Notice>
      </div>
    );
  }
  if (!value) {
    return (
      <p className={appStyles.muted} role="status">
        <Spinner /> <span>{t("loading")}</span>
      </p>
    );
  }

  const r = value;
  const dates = range(r.start_date, r.end_date);
  const top = Math.max(0, ...r.by_category.map((c) => Number(c.amount.amount)));
  const widest = Math.max(0, ...r.members.map((m) => Math.abs(Number(m.net.amount))));
  const file = (name: "expenses.csv" | "balances.csv" | "settlements.csv" | "export.pdf") =>
    tripReportApi.fileUrl(id, name, filters, locale);
  const pdfOver = r.expense_count > r.limits.pdf_rows;
  const date = (d: string) => formatDate(`${d}T12:00:00Z`, locale, "UTC");
  const who = (p: { user_id: string; display_name: string }) =>
    p.user_id === user.id ? t("you") : p.display_name;

  return (
    <div className={s.page}>
      <div>{back}</div>
      <div className={s.head}>
        <div>
          <p className={s.eyebrow}>{t("title")}</p>
          <h1 className={appStyles.pageTitle} dir="auto">
            {r.name}
          </h1>
          <p className={s.meta}>
            {r.destination ? <span dir="auto">{r.destination}</span> : null}
            {dates ? <span>{dates}</span> : null}
            <span>
              {t("currency")} <bdi dir="ltr">{r.base_currency}</bdi>
            </span>
            <span>{t("members", { count: r.members.length })}</span>
          </p>
        </div>
        <div className={cn(s.headActions, s.noPrint)}>
          <Button
            onClick={() => exporter.download(file("expenses.csv"), "trip-expenses.csv", false)}
            busy={exporter.busy === file("expenses.csv")}
          >
            <Download aria-hidden="true" />
            {t("csv.expenses")}
          </Button>
          <Button
            onClick={() => exporter.download(file("balances.csv"), "trip-balances.csv", false)}
            busy={exporter.busy === file("balances.csv")}
          >
            <Download aria-hidden="true" />
            {t("csv.balances")}
          </Button>
          <Button
            onClick={() =>
              exporter.download(file("settlements.csv"), "trip-settlements.csv", false)
            }
            busy={exporter.busy === file("settlements.csv")}
          >
            <Download aria-hidden="true" />
            {t("csv.settlements")}
          </Button>
          <Button
            onClick={() => exporter.download(file("export.pdf"), "trip-report.pdf", true)}
            busy={exporter.busy === file("export.pdf")}
          >
            <FileText aria-hidden="true" />
            {reports("export.pdf")}
          </Button>
          <Button
            onClick={() => void exporter.print(file("export.pdf"))}
            busy={exporter.busy === "print"}
          >
            <Printer aria-hidden="true" />
            {reports("export.print")}
          </Button>
        </div>
      </div>

      {exporter.error ? <Notice tone="error">{exporter.error}</Notice> : null}
      {exporter.done ? (
        <Notice tone={exporter.done.partial ? "info" : "success"}>{exporter.done.text}</Notice>
      ) : null}
      {pdfOver ? (
        <Notice tone="info">
          {t("pdfLimit", { limit: r.limits.pdf_rows, total: r.expense_count })}
        </Notice>
      ) : null}
      {r.scope === "mine" ? <Notice>{t("former")}</Notice> : null}
      <p className={s.note}>{t("privacy")}</p>

      <form
        id={formId}
        className={cn(s.card, s.filters, s.noPrint)}
        onSubmit={(e) => {
          e.preventDefault();
          setFilters({ ...draft });
        }}
        aria-label={t("filters.label")}
      >
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${formId}-from`}>
            {t("filters.from")}
          </label>
          <input
            id={`${formId}-from`}
            type="date"
            className={appStyles.input}
            value={draft.from ?? ""}
            onChange={(e) => setDraft((d) => ({ ...d, from: e.target.value || undefined }))}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${formId}-to`}>
            {t("filters.to")}
          </label>
          <input
            id={`${formId}-to`}
            type="date"
            className={appStyles.input}
            value={draft.to ?? ""}
            onChange={(e) => setDraft((d) => ({ ...d, to: e.target.value || undefined }))}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${formId}-category`}>
            {t("filters.category")}
          </label>
          <Select
            id={`${formId}-category`}
            value={draft.category ?? ""}
            onValueChange={(value) => setDraft((d) => ({ ...d, category: value || undefined }))}
          >
            <option value="">{t("filters.all")}</option>
            {TRIP_CATEGORIES.map((key) => (
              <option key={key} value={key}>
                {categoryName(key)}
              </option>
            ))}
          </Select>
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${formId}-member`}>
            {t("filters.member")}
          </label>
          <Select
            id={`${formId}-member`}
            value={draft.member_id ?? ""}
            onValueChange={(value) => setDraft((d) => ({ ...d, member_id: value || undefined }))}
          >
            <option value="">{t("filters.everyone")}</option>
            {r.members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {who(m)}
              </option>
            ))}
          </Select>
        </div>
        <div className={s.filterActions}>
          <Button type="submit" variant="primary" busy={loading}>
            {t("filters.apply")}
          </Button>
          {r.filtered ? (
            <Button
              variant="ghost"
              onClick={() => {
                setDraft({});
                setFilters({});
              }}
            >
              {t("filters.clear")}
            </Button>
          ) : null}
        </div>
      </form>
      {r.filtered ? <Notice tone="info">{t("filteredNote")}</Notice> : null}

      <section className={s.card} aria-labelledby="report-summary">
        <div className={s.cardHead}>
          <h2 id="report-summary" className={s.cardTitle}>
            {t("summary")}
          </h2>
          {r.balanced && isZero(r.outstanding) ? (
            <span className={s.check}>
              <CircleCheck aria-hidden="true" />
              {t("allSettled")}
            </span>
          ) : null}
        </div>
        <div className={s.cardBody}>
          <dl className={s.figures}>
            <div>
              <dt>{t("total")}</dt>
              <dd>
                <Money value={r.total} />
              </dd>
            </div>
            <div>
              <dt>{t("expenseCount")}</dt>
              <dd>{r.expense_count}</dd>
            </div>
            <div>
              <dt>{t("outstanding")}</dt>
              <dd>
                <Money value={r.outstanding} />
              </dd>
            </div>
          </dl>
          {r.pending_expenses > 0 ? (
            <p className={s.note} style={{ marginBlockStart: 8 }}>
              {t("pendingExpenses", { count: r.pending_expenses })}
            </p>
          ) : null}
        </div>
      </section>

      <div className={s.layout}>
        <div className={s.main}>
          <section className={s.card} aria-labelledby="report-categories">
            <div className={s.cardHead}>
              <h2 id="report-categories" className={s.cardTitle}>
                {t("byCategory")}
              </h2>
            </div>
            <div className={s.cardBody}>
              {r.by_category.length === 0 ? (
                <p className={s.note}>{t("none")}</p>
              ) : (
                <table className={s.reportTable}>
                  <caption className={appStyles.srOnly}>{t("byCategory")}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{t("col.category")}</th>
                      <th scope="col" className={s.num}>
                        {t("col.amount")}
                      </th>
                      <th scope="col" className={s.num}>
                        {t("col.percent")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.by_category.map((c) => (
                      <tr key={c.category_key}>
                        <td>
                          <span className={s.catCell}>
                            <TripCategoryIcon categoryKey={c.category_key} />
                            <span>{categoryName(c.category_key)}</span>
                          </span>
                          <span className={s.bar} aria-hidden="true">
                            <span
                              style={{
                                inlineSize: `${top ? (Number(c.amount.amount) / top) * 100 : 0}%`,
                              }}
                            />
                          </span>
                        </td>
                        <td className={s.num}>
                          <Money value={c.amount} />
                        </td>
                        <td className={s.num}>
                          <bdi dir="ltr">{`${c.percent}%`}</bdi>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section className={s.card} aria-labelledby="report-expenses">
            <div className={s.cardHead}>
              <h2 id="report-expenses" className={s.cardTitle}>
                {t("expenses")}
              </h2>
              <span className={s.note}>{t("expenseCountShort", { count: r.expense_count })}</span>
            </div>
            <div className={cn(s.cardBody, s.scrollX)}>
              {r.expenses.length === 0 ? (
                <p className={s.note}>{t("none")}</p>
              ) : (
                <table className={s.reportTable}>
                  <caption className={appStyles.srOnly}>{t("expenses")}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{t("col.date")}</th>
                      <th scope="col">{t("col.description")}</th>
                      <th scope="col">{t("col.paidBy")}</th>
                      <th scope="col" className={s.num}>
                        {t("col.amount")}
                      </th>
                      <th scope="col">{t("col.sharedBy")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.expenses.map((e) => (
                      <tr key={e.id}>
                        <td className={s.nowrap}>
                          <bdi>{date(e.expense_date)}</bdi>
                        </td>
                        <td>
                          <span dir="auto">{e.description}</span>
                          <span className={s.cellSub}>{categoryName(e.category_key)}</span>
                        </td>
                        <td dir="auto">{who(e.payer)}</td>
                        <td className={s.num}>
                          <Money value={e.base_amount} />
                          {e.amount.currency !== e.base_amount.currency ? (
                            <span className={s.cellSub}>
                              <Money value={e.amount} />
                            </span>
                          ) : null}
                        </td>
                        <td>
                          <span className={s.cellSub} dir="auto">
                            {e.shares.map((x) => isolate(who(x.person))).join(", ")}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>
        </div>

        <div className={s.side}>
          <section className={s.card} aria-labelledby="report-members">
            <div className={s.cardHead}>
              <h2 id="report-members" className={s.cardTitle}>
                {t("byMember")}
              </h2>
            </div>
            <div className={s.cardBody}>
              <ul className={s.balances}>
                {r.members.map((m) => {
                  const negative = isNegative(m.net);
                  const zero = isZero(m.net);
                  const abs = formatMoney(
                    { ...m.net, amount: m.net.amount.replace(/^-/, "") },
                    locale,
                  );
                  const width = widest ? (Math.abs(Number(m.net.amount)) / widest) * 50 : 0;
                  return (
                    <li key={m.user_id} className={s.reportMember}>
                      <span className={s.balanceName} dir="auto">
                        {who(m)}
                        {/* eslint-disable-next-line no-restricted-syntax -- "left" is a membership status, not a CSS side */}
                        {m.status === "left" || m.status === "removed"
                          ? ` · ${t(`status.${m.status}`)}`
                          : ""}
                      </span>
                      <span
                        className={s.net}
                        data-sign={zero ? undefined : negative ? "neg" : "pos"}
                      >
                        {zero
                          ? t("settled")
                          : negative
                            ? t("owes", { amount: abs })
                            : t("isOwed", { amount: abs })}
                      </span>
                      <span
                        className={s.netBar}
                        aria-hidden="true"
                        data-sign={negative ? "neg" : "pos"}
                      >
                        <span style={{ inlineSize: `${width}%` }} />
                      </span>
                      <span className={s.balanceSub}>
                        {t("memberLine", {
                          paid: formatMoney(m.paid, locale),
                          share: formatMoney(m.benefited, locale),
                          sent: formatMoney(m.sent, locale),
                          received: formatMoney(m.received, locale),
                        })}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          </section>

          <section className={s.card} aria-labelledby="report-payments">
            <div className={s.cardHead}>
              <h2 id="report-payments" className={s.cardTitle}>
                {t("payments")}
              </h2>
            </div>
            <div className={s.cardBody}>
              {r.settlements_confirmed.length === 0 ? (
                <p className={s.note}>{t("noPayments")}</p>
              ) : (
                <ul className={s.payments}>
                  {r.settlements_confirmed.map((p) => (
                    <li key={p.id} className={s.suggestionRow}>
                      <span>
                        {t(p.kind === "reversal" ? "reversalLine" : "paymentLine", {
                          from: isolate(who(p.payer)),
                          to: isolate(who(p.recipient)),
                        })}
                        <span className={s.cellSub}>
                          <bdi>{date(p.paid_on)}</bdi>
                          {p.status === "reversed" ? ` · ${t("reversed")}` : ""}
                        </span>
                      </span>
                      <Money value={p.amount} />
                    </li>
                  ))}
                </ul>
              )}
              {r.settlements_pending.length > 0 ? (
                <>
                  <h3 className={s.subhead}>{t("pendingPayments")}</h3>
                  <ul className={s.payments}>
                    {r.settlements_pending.map((p) => (
                      <li key={p.id} className={s.suggestionRow}>
                        <span>
                          {t("paymentLine", {
                            from: isolate(who(p.payer)),
                            to: isolate(who(p.recipient)),
                          })}
                        </span>
                        <Money value={p.amount} />
                      </li>
                    ))}
                  </ul>
                  <p className={s.note}>{t("pendingNote")}</p>
                </>
              ) : null}
            </div>
          </section>

          <section className={s.card} aria-labelledby="report-suggestions">
            <div className={s.cardHead}>
              <h2 id="report-suggestions" className={s.cardTitle}>
                {t("suggestions")}
              </h2>
            </div>
            <div className={s.cardBody}>
              {r.suggestions.length === 0 ? (
                <p className={s.note}>{t("nothingToSettle")}</p>
              ) : (
                <ul className={s.suggestions}>
                  {r.suggestions.map((x, i) => (
                    <li
                      key={`${x.payer.user_id}-${x.recipient.user_id}-${i}`}
                      className={s.suggestionRow}
                    >
                      <span>
                        {t("paymentLine", {
                          from: isolate(who(x.payer)),
                          to: isolate(who(x.recipient)),
                        })}
                      </span>
                      <Money value={x.amount} />
                    </li>
                  ))}
                </ul>
              )}
              <p className={s.note}>{t("suggestionsNote")}</p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
