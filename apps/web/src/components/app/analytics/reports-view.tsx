"use client";

import { CircleAlert, CircleCheck, Download, FileText, Printer } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useId, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import {
  type AllocationView,
  type CurrencyReport,
  type Grain,
  type Report,
  type ReportFilters,
  reportsApi,
} from "@/lib/analytics";
import { financeApi, type Transaction, type TransactionType } from "@/lib/finance";
import { formatDate, formatDateTime, localeTag } from "@/lib/format";
import { formatMoney, isNegative, type Money as MoneyValue } from "@/lib/money";
import { cn } from "@/lib/utils";
import { Money, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { signedAmount, useAccountName, useCategoryName } from "../finance/parts";
import { TransactionDialog, useTxnDialog } from "../finance/transaction-dialog";
import { Button, Loading, Notice } from "../ui";
import s from "./analytics.module.css";
import { CategoryBars, CategoryTable, SeriesTable, useBucketLabel } from "./charts";
import { Kpis } from "./insights-view";
import {
  AccountSelect,
  AllocationSelect,
  CurrencySelect,
  customInvalid,
  type Period,
  PeriodPicker,
  periodQuery,
} from "./filters";
import { Select } from "../select";

/**
 * Reports (API_SPEC.md §13). The API calculates every section from the ledger in one consistent
 * snapshot; this page only lays them out. The same filters drive the PDF and CSV exports.
 */
/** `embedded`: a tab of the Analytics page, so its title is a section heading, not the page's. */
export function ReportsView({ embedded = false }: { embedded?: boolean } = {}) {
  return (
    <FinanceGate>
      <ReportsPage embedded={embedded} />
    </FinanceGate>
  );
}

const TYPES: readonly TransactionType[] = [
  "expense",
  "income",
  "transfer",
  "adjustment",
  "opening_balance",
  "debt_start",
  "debt_payment",
  "trip_expense_payment",
  "trip_settlement_paid",
  "trip_settlement_received",
  "refund",
];

type Draft = {
  period: Period;
  type: string;
  accountId: string;
  categoryId: string;
  currency: string;
  q: string;
  grain: "" | Grain;
  allocation: AllocationView;
};

function ReportsPage({ embedded }: { embedded: boolean }) {
  const t = useTranslations("app.reports");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { revision, categories, accounts, currencies } = useFinance();
  const categoryName = useCategoryName();
  const id = useId();
  const today = todayIn(user.time_zone);
  const [draft, setDraft] = useState<Draft>({
    period: { key: "month", from: "", to: "" },
    type: "",
    accountId: "",
    categoryId: "",
    currency: "",
    q: "",
    grain: "",
    allocation: "include",
  });
  const [q, setQ] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{ for: string; value?: Report; failed?: boolean } | null>(null);
  const [exporting, setExporting] = useState<"pdf" | "csv" | "print" | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportDone, setExportDone] = useState<{ partial: boolean; text: string } | null>(null);
  // Asked first when the table is longer than a PDF (and so a printout) can hold.
  const [confirming, setConfirming] = useState<"pdf" | "print" | null>(null);
  const [loaded, setLoaded] = useState(0);
  const txnDialog = useTxnDialog();

  // The description search waits for a pause in typing.
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(draft.q.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [draft.q]);

  const invalid = customInvalid(draft.period);
  const filters: ReportFilters = {
    ...periodQuery(draft.period, today),
    type: (draft.type || undefined) as TransactionType | undefined,
    account_id: draft.accountId || undefined,
    category_id: draft.categoryId || undefined,
    currency: draft.currency || undefined,
    q: q || undefined,
    trip_allocation: draft.allocation === "include" ? undefined : draft.allocation,
  };
  const withGrain = { ...filters, ...(draft.grain ? { grain: draft.grain } : {}) };
  const request = JSON.stringify([withGrain, revision, attempt]);

  useEffect(() => {
    if (invalid) return;
    let cancelled = false;
    reportsApi
      .get(withGrain)
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
  }, [request, invalid, handleAuthError]);

  const value = data?.value;
  const stale = data !== null && data.for !== request;
  const set = (changes: Partial<Draft>) => setDraft((d) => ({ ...d, ...changes }));
  const codes = [...new Set([...accounts.map((a) => a.currency), ...currencies.map((c) => c.code)])]
    .filter((code) => accounts.some((a) => a.currency === code) || code === draft.currency)
    .sort();

  const number = (n: number) => new Intl.NumberFormat(localeTag(locale)).format(n);
  const overLimit =
    !!value?.limits && value.table_count > value.limits.pdf_rows ? value.limits.pdf_rows : null;

  const failure = async (response: Response): Promise<string> => {
    const body = (await response.json().catch(() => null)) as { code?: string } | null;
    const code = body?.code;
    return response.status === 429
      ? t("export.rateLimited")
      : code === "export_too_large"
        ? t("export.tooLarge")
        : code === "pdf_busy"
          ? t("export.busy")
          : code === "pdf_unavailable"
            ? t("export.pdfUnavailable")
            : t("export.failed");
  };

  /** What the PDF says about itself (the API's headers), shown after it's made. */
  const completeness = (response: Response) => {
    const included = Number(response.headers.get("X-Report-Rows-Included") ?? 0);
    const total = Number(response.headers.get("X-Report-Rows-Total") ?? included);
    const partial = response.headers.get("X-Report-Complete") === "false";
    setExportDone({
      partial,
      text: partial
        ? t("export.partialDone", { included: number(included), total: number(total) })
        : t("export.completeDone", { total: number(total) }),
    });
  };

  const download = async (kind: "pdf" | "csv") => {
    setConfirming(null);
    setExporting(kind);
    setExportError(null);
    setExportDone(null);
    try {
      const url =
        kind === "pdf"
          ? reportsApi.pdfUrl(withGrain, locale)
          : reportsApi.csvUrl(withGrain, locale);
      const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) {
        setExportError(await failure(response));
        return;
      }
      if (kind === "pdf") completeness(response);
      const blob = await response.blob();
      const name =
        /filename="?([^";]+)"?/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ??
        `finpilot-report.${kind}`;
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = name;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch {
      setExportError(t("export.failed"));
    } finally {
      setExporting(null);
    }
  };

  /**
   * Print the complete report: the same PDF the API makes from the same query and filters,
   * opened for printing. Never the rows the browser happens to have loaded. The window is opened
   * at the click (so pop-up blockers allow it) and filled when the PDF arrives.
   */
  const print = async () => {
    setConfirming(null);
    setExportError(null);
    setExportDone(null);
    const win = window.open("", "_blank");
    if (!win) {
      setExportError(t("export.popupBlocked"));
      return;
    }
    win.document.title = t("export.printWindow");
    win.document.body.textContent = t("export.printing");
    setExporting("print");
    try {
      const response = await fetch(reportsApi.pdfUrl(withGrain, locale), {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        win.close();
        setExportError(await failure(response));
        return;
      }
      completeness(response);
      const href = URL.createObjectURL(
        new Blob([await response.blob()], { type: "application/pdf" }),
      );
      win.location.href = href;
      window.setTimeout(() => URL.revokeObjectURL(href), 120_000);
    } catch {
      win.close();
      setExportError(t("export.failed"));
    } finally {
      setExporting(null);
    }
  };

  const start = (kind: "pdf" | "print") => {
    if (overLimit !== null) setConfirming(kind);
    else if (kind === "pdf") void download("pdf");
    else void print();
  };

  const active = categories.filter((c) => c.status === "active");

  return (
    <div className={s.page}>
      <div className={s.head}>
        <div>
          {embedded ? (
            <h2 className={s.sectionTitle}>{t("title")}</h2>
          ) : (
            <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          )}
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <div className={cn(s.headActions, s.noPrint)}>
          <Button onClick={() => start("pdf")} busy={exporting === "pdf"} disabled={!value}>
            <FileText aria-hidden="true" />
            {t("export.pdf")}
          </Button>
          <Button onClick={() => download("csv")} busy={exporting === "csv"} disabled={!value}>
            <Download aria-hidden="true" />
            {t("export.csv")}
          </Button>
          <Button
            variant="ghost"
            onClick={() => start("print")}
            busy={exporting === "print"}
            disabled={!value}
          >
            <Printer aria-hidden="true" />
            {t("export.print")}
          </Button>
        </div>
      </div>
      {exportError ? (
        <Notice tone="error" className={s.noPrint}>
          {exportError}
        </Notice>
      ) : null}
      {exportDone ? (
        <Notice tone={exportDone.partial ? "info" : "success"} className={s.noPrint}>
          {exportDone.text}
        </Notice>
      ) : null}
      {confirming && value && overLimit !== null ? (
        <div
          className={cn(s.limitPanel, s.noPrint)}
          role="alertdialog"
          aria-labelledby={`${id}-limit`}
        >
          <p id={`${id}-limit`} className={s.limitTitle}>
            {t("export.limitTitle")}
          </p>
          <p className={s.note}>
            {t("export.limitBody", {
              total: number(value.table_count),
              limit: number(overLimit),
            })}
          </p>
          <div className={s.headActions}>
            <Button variant="primary" onClick={() => download("csv")} autoFocus>
              {t("export.csvInstead")}
            </Button>
            <Button onClick={() => (confirming === "pdf" ? download("pdf") : print())}>
              {confirming === "pdf" ? t("export.continuePdf") : t("export.continuePrint")}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              {t("export.cancel")}
            </Button>
          </div>
        </div>
      ) : null}
      {value && loaded < value.table_count ? (
        <p className={cn(s.printWarning, s.printOnly)}>
          {t("details.browserPrint", {
            shown: number(loaded),
            total: number(value.table_count),
          })}
        </p>
      ) : null}

      <div className={cn(s.filterBar, s.noPrint)}>
        <PeriodPicker value={draft.period} onChange={(period) => set({ period })} today={today} />
        <div className={s.filterField}>
          <label className={s.filterLabel} htmlFor={`${id}-type`}>
            {t("filters.type")}
          </label>
          <Select
            id={`${id}-type`}
            value={draft.type}
            onValueChange={(value) => set({ type: value })}
          >
            <option value="">{t("filters.allTypes")}</option>
            {TYPES.map((type) => (
              <option key={type} value={type}>
                {t(`types.${type}`)}
              </option>
            ))}
          </Select>
        </div>
        <AccountSelect value={draft.accountId} onChange={(accountId) => set({ accountId })} />
        <AllocationSelect value={draft.allocation} onChange={(allocation) => set({ allocation })} />
        <div className={s.filterField}>
          <label className={s.filterLabel} htmlFor={`${id}-cat`}>
            {t("filters.category")}
          </label>
          <Select
            id={`${id}-cat`}
            value={draft.categoryId}
            onValueChange={(value) => set({ categoryId: value })}
          >
            <option value="">{t("filters.allCategories")}</option>
            {active.map((c) => (
              <option key={c.id} value={c.id}>
                {categoryName(c)}
              </option>
            ))}
          </Select>
        </div>
        <CurrencySelect
          value={draft.currency}
          onChange={(currency) => set({ currency })}
          codes={codes}
          allowAll
        />
        <div className={cn(s.filterField, s.wide)}>
          <label className={s.filterLabel} htmlFor={`${id}-q`}>
            {t("filters.search")}
          </label>
          <input
            id={`${id}-q`}
            type="search"
            className={appStyles.input}
            dir="auto"
            maxLength={100}
            value={draft.q}
            onChange={(e) => set({ q: e.target.value })}
            aria-describedby={`${id}-q-hint`}
          />
          <span id={`${id}-q-hint`} className={appStyles.hint}>
            {t("filters.searchHint")}
          </span>
        </div>
      </div>

      {data?.failed ? (
        <Notice tone="error">
          {t("loadFailed")}{" "}
          <button
            type="button"
            className={appStyles.linkBtn}
            onClick={() => setAttempt((n) => n + 1)}
          >
            {t("retry")}
          </button>
        </Notice>
      ) : !value ? (
        <Loading label={t("loading")} />
      ) : (
        <div className={cn(s.page, stale && fs.refreshing)} aria-busy={stale || undefined}>
          <ReportMeta report={value} />
          <SummarySection report={value} />
          <CategoriesSection report={value} />
          <BreakdownSection
            report={value}
            grain={draft.grain}
            onGrain={(grain) => set({ grain })}
          />
          <DetailsSection
            report={value}
            q={q}
            onOpen={(txnId) => txnDialog.show(txnId)}
            onLoaded={setLoaded}
            key={JSON.stringify(withGrain) + revision}
          />
          <RecurringSection report={value} />
          <DebtsSection report={value} />
          <p className={cn(s.note, s.printOnly)}>
            {t("printedAt", {
              at: formatDateTime(new Date().toISOString(), locale, user.time_zone),
            })}
          </p>
        </div>
      )}
      <TransactionDialog dialog={txnDialog} />
    </div>
  );
}

function Section({
  id,
  title,
  aside,
  children,
}: {
  id: string;
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={s.reportSection} aria-labelledby={`${id}-title`}>
      <div className={s.sectionHead}>
        <h2 id={`${id}-title`}>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function ReportMeta({ report }: { report: Report }) {
  const t = useTranslations("app.reports");
  const locale = useLocale() as Locale;
  const label = useBucketLabel();
  const f = report.filters;
  return (
    <div className={s.reportMeta}>
      <span className={s.printOnly}>
        <strong>{t("brand")}</strong>
      </span>
      <span>
        {t("periodLine", {
          from: label(f.date_from, "day", "long"),
          to: label(f.date_to, "day", "long"),
        })}
      </span>
      <span>
        {t("generated", {
          at: formatDateTime(report.generated_at, locale, report.time_zone),
        })}
      </span>
      <span>{t("timeZone", { zone: report.time_zone })}</span>
      <span>
        {t("count", {
          count: report.transaction_count,
          formatted: new Intl.NumberFormat(localeTag(locale)).format(report.transaction_count),
        })}
      </span>
    </div>
  );
}

function SummarySection({ report }: { report: Report }) {
  const t = useTranslations("app.reports");
  return (
    <Section id="summary" title={t("summary.title")}>
      <p className={s.note}>{t("summary.netDefinition")}</p>
      <p className={s.note}>{t("summary.scope")}</p>
      {report.by_currency.length === 0 ? <p className={s.note}>{t("none")}</p> : null}
      {report.by_currency.map((c) => (
        <div key={c.currency} className={s.currencyBlock}>
          {report.by_currency.length > 1 ? (
            <h3 className={s.currencyTitle}>
              <bdi dir="ltr">{c.currency}</bdi>
            </h3>
          ) : null}
          <Kpis totals={c.totals} previous={c.previous} count="transactions" />
          <TripShares report={c} />
          {c.balances ? (
            <BalanceTable report={c} />
          ) : (
            <p className={s.note}>{t("summary.balancesHidden")}</p>
          )}
        </div>
      ))}
      {report.by_currency.length > 1 ? <p className={s.note}>{t("separate")}</p> : null}
    </Section>
  );
}

const BALANCE_ROWS = [
  "initial_balances",
  "income",
  "expenses",
  "transfers_in",
  "transfers_out",
  "conversions_in",
  "conversions_out",
  "adjustments",
  "debts_in",
  "debts_out",
  "refunds",
  "trips_in",
  "trips_out",
] as const;
const OUTGOING = new Set([
  "expenses",
  "transfers_out",
  "conversions_out",
  "debts_out",
  "trips_out",
]);

/** Spending's two parts (§11.7): everyday spending, and allocated trip shares by trip. */
function TripShares({ report }: { report: CurrencyReport }) {
  const t = useTranslations("app.reports.tripShares");
  const share = report.totals.trip_shares;
  if (!share || /^0(\.0+)?$/.test(share.amount)) return null;
  return (
    <div className={s.tripShares}>
      <p className={s.note}>
        {t.rich("split", {
          everyday: () => <Money value={report.totals.non_trip_expenses ?? share} />,
          shares: () => <Money value={share} />,
        })}
      </p>
      {report.trips?.length ? (
        <ul className={s.tripShareList}>
          {report.trips.map((trip) => (
            <li key={trip.trip_id}>
              <Link href={`/app/trips/${trip.trip_id}`} className={appStyles.linkBtn}>
                <bdi>{trip.trip_name}</bdi>
              </Link>
              <span className={s.note}>{t("count", { count: trip.count })}</span>
              <Money value={trip.amount} />
            </li>
          ))}
        </ul>
      ) : null}
      <p className={s.note}>{t("note")}</p>
    </div>
  );
}

function BalanceTable({ report }: { report: CurrencyReport }) {
  const t = useTranslations("app.reports.balances");
  const b = report.balances;
  if (!b) return null;
  const negate = (m: MoneyValue): MoneyValue =>
    m.amount === "0" || /^0(\.0+)?$/.test(m.amount) ? m : { ...m, amount: `-${m.amount}` };
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <caption>
          <span className={s.reconcile} data-ok={b.reconciles}>
            {b.reconciles ? <CircleCheck aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}
            {b.reconciles ? t("reconciles") : t("doesNotReconcile")}
          </span>
        </caption>
        <thead>
          <tr>
            <th scope="col">{t("movement")}</th>
            <th scope="col" className={s.num}>
              <bdi dir="ltr">{report.currency}</bdi>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr data-total="">
            <th scope="row">{t("opening")}</th>
            <td className={s.num}>
              <Money value={b.opening} />
            </td>
          </tr>
          {BALANCE_ROWS.filter((row) => !/^-?0(\.0+)?$/.test(b[row].amount)).map((row) => (
            <tr key={row}>
              <th scope="row">{t(row)}</th>
              <td className={s.num}>
                <Money value={OUTGOING.has(row) ? negate(b[row]) : b[row]} signed />
              </td>
            </tr>
          ))}
          <tr data-total="">
            <th scope="row">{t("closing")}</th>
            <td className={s.num}>
              <Money value={b.closing} />
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function CategoriesSection({ report }: { report: Report }) {
  const t = useTranslations("app.reports");
  return (
    <Section id="categories" title={t("categories.title")}>
      <p className={s.note}>{t("categories.note")}</p>
      {report.by_currency.every((c) => c.categories.length === 0) ? (
        <p className={s.note}>{t("none")}</p>
      ) : null}
      {report.by_currency
        .filter((c) => c.categories.length > 0)
        .map((c) => (
          <div key={c.currency} className={s.grid2}>
            <div>
              {report.by_currency.length > 1 ? (
                <h3 className={s.currencyTitle}>
                  <bdi dir="ltr">{c.currency}</bdi>
                </h3>
              ) : null}
              <CategoryBars items={c.categories} />
            </div>
            <CategoryTable
              items={c.categories}
              caption={t("categories.caption", { currency: c.currency })}
            />
          </div>
        ))}
    </Section>
  );
}

function BreakdownSection({
  report,
  grain,
  onGrain,
}: {
  report: Report;
  grain: "" | Grain;
  onGrain: (grain: Grain) => void;
}) {
  const t = useTranslations("app.reports");
  const it = useTranslations("app.insights");
  const shown = report.by_currency[0]?.grain ?? "day";
  return (
    <Section
      id="breakdown"
      title={t("breakdown.title")}
      aside={
        <span className={cn(s.viewToggle, s.noPrint)} role="group" aria-label={t("breakdown.by")}>
          {(["day", "week", "month"] as const).map((g) => (
            <button
              key={g}
              type="button"
              aria-pressed={(grain || shown) === g}
              onClick={() => onGrain(g)}
            >
              {it(`grain.${g}`)}
            </button>
          ))}
        </span>
      }
    >
      {report.by_currency.length === 0 ? <p className={s.note}>{t("none")}</p> : null}
      {report.by_currency.map((c) => {
        const points = c.series.filter(
          (p) => !/^0(\.0+)?$/.test(p.income.amount) || !/^0(\.0+)?$/.test(p.expenses.amount),
        );
        // A currency with only balance movements has no income or spending to tabulate.
        return points.length === 0 ? (
          <p key={c.currency} className={s.note}>
            {t("breakdown.empty", { currency: c.currency })}
          </p>
        ) : (
          <SeriesTable
            key={c.currency}
            points={points}
            grain={c.grain}
            caption={t("breakdown.caption", { currency: c.currency })}
          />
        );
      })}
    </Section>
  );
}

function DetailsSection({
  report,
  q,
  onOpen,
  onLoaded,
}: {
  report: Report;
  q: string;
  onOpen: (id: string) => void;
  onLoaded: (count: number) => void;
}) {
  const t = useTranslations("app.reports.details");
  const rt = useTranslations("app.reports");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const categoryName = useCategoryName();
  const accountName = useAccountName();
  const [items, setItems] = useState<Transaction[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const f = report.filters;

  const load = (after: string | null) =>
    financeApi.history(
      {
        type: (f.type ?? undefined) as TransactionType | undefined,
        account_id: f.account_id ?? undefined,
        category_id: f.category_id ?? undefined,
        currency: f.currency ?? undefined,
        from: f.date_from,
        to: f.date_to,
        q: q || undefined,
      },
      after,
      50,
    );

  useEffect(() => {
    let cancelled = false;
    load(null)
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        onLoaded(page.items.length);
        setCursor(page.next_cursor);
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
    // Mounted afresh (keyed) whenever the filters change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const more = async () => {
    setBusy(true);
    try {
      const page = await load(cursor);
      setItems((prev) => {
        const next = [...(prev ?? []), ...page.items];
        onLoaded(next.length);
        return next;
      });
      setCursor(page.next_cursor);
    } catch (error) {
      if (!handleAuthError(error)) setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const rate = (txn: Transaction) => {
    if (!txn.fx || !txn.original_amount || !txn.account) return null;
    const number = new Intl.NumberFormat(localeTag(locale), { maximumSignificantDigits: 8 });
    return t("rate", {
      from: txn.original_amount.currency,
      to: txn.account.currency,
      rate: number.format(Number(txn.fx.rate)),
      method: t(`methods.${txn.fx.method}`),
    });
  };

  return (
    <Section id="details" title={t("title")}>
      {q ? (
        <p className={s.narrowed} role="status">
          {t("narrowed", {
            q,
            count: new Intl.NumberFormat(localeTag(locale)).format(report.table_count),
            total: new Intl.NumberFormat(localeTag(locale)).format(report.transaction_count),
          })}
        </p>
      ) : null}
      {failed ? (
        <Notice tone="error">{rt("loadFailed")}</Notice>
      ) : !items ? (
        <Loading label={rt("loading")} />
      ) : items.length === 0 ? (
        <p className={s.note}>{rt("none")}</p>
      ) : (
        <>
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th scope="col">{t("date")}</th>
                  <th scope="col">{t("description")}</th>
                  <th scope="col">{t("category")}</th>
                  <th scope="col">{t("account")}</th>
                  <th scope="col" className={s.num}>
                    {t("amount")}
                  </th>
                  <th scope="col" className={s.num}>
                    {t("original")}
                  </th>
                  <th scope="col">{t("conversion")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((txn) => (
                  <tr key={txn.id} data-voided={txn.status === "reversed" ? "" : undefined}>
                    <td>
                      <bdi>{formatDate(txn.effective_date, locale, "UTC")}</bdi>
                    </td>
                    <td dir="auto">
                      <button
                        type="button"
                        className={cn(appStyles.linkBtn, s.noPrintLink)}
                        onClick={() => onOpen(txn.id)}
                      >
                        {txn.description || rt(`types.${txn.type}`)}
                      </button>
                    </td>
                    <td dir="auto">{txn.category ? categoryName(txn.category) : "—"}</td>
                    <td dir="auto">
                      {txn.account ? accountName(txn.account) : "—"}
                      {txn.counter_account ? ` → ${accountName(txn.counter_account)}` : ""}
                    </td>
                    <td className={s.num}>
                      <Money value={signedAmount(txn)} signed />
                    </td>
                    <td className={s.num}>
                      {txn.original_amount ? (
                        <bdi dir="ltr">{formatMoney(txn.original_amount, locale)}</bdi>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className={appStyles.small}>{rate(txn) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {cursor ? (
            <Button className={cn(s.loadMore, s.noPrint)} onClick={more} busy={busy}>
              {t("more")}
            </Button>
          ) : null}
          <p className={s.note}>
            {t("shown", {
              shown: new Intl.NumberFormat(localeTag(locale)).format(items.length),
              total: new Intl.NumberFormat(localeTag(locale)).format(report.table_count),
            })}
          </p>
        </>
      )}
    </Section>
  );
}

function RecurringSection({ report }: { report: Report }) {
  const t = useTranslations("app.reports.recurring");
  const rt = useTranslations("app.recurring");
  const locale = useLocale() as Locale;
  return (
    <Section id="recurring" title={t("title")}>
      <p className={s.note}>{t("note")}</p>
      {report.recurring.length === 0 ? (
        <p className={s.note}>{t("none")}</p>
      ) : (
        <div className={s.tableWrap}>
          <table className={s.table}>
            <thead>
              <tr>
                <th scope="col">{t("name")}</th>
                <th scope="col">{t("window")}</th>
                <th scope="col">{t("status")}</th>
                <th scope="col" className={s.num}>
                  {t("expected")}
                </th>
                <th scope="col" className={s.num}>
                  {t("posted")}
                </th>
              </tr>
            </thead>
            <tbody>
              {report.recurring.map((o, i) => (
                <tr key={`${o.name}-${o.window_start}-${i}`}>
                  <th scope="row" dir="auto">
                    {o.name}
                  </th>
                  <td>
                    <bdi>
                      {o.window_start === o.window_end
                        ? formatDate(o.window_start, locale, "UTC")
                        : `${formatDate(o.window_start, locale, "UTC")} – ${formatDate(o.window_end, locale, "UTC")}`}
                    </bdi>
                  </td>
                  <td>
                    <span className={s.tag} data-state={o.state}>
                      {rt(`states.${o.state}`)}
                    </span>
                  </td>
                  <td className={cn(s.num, appStyles.muted)}>
                    {o.expected ? <Money value={o.expected} /> : t("variable")}
                  </td>
                  <td className={s.num}>{o.actual ? <Money value={o.actual} /> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function DebtsSection({ report }: { report: Report }) {
  const t = useTranslations("app.reports.debts");
  return (
    <Section id="debts" title={t("title")}>
      <p className={s.note}>{t("note")}</p>
      {report.debts.length === 0 ? (
        <p className={s.note}>{t("none")}</p>
      ) : (
        <div className={s.tableWrap}>
          <table className={s.table}>
            <thead>
              <tr>
                <th scope="col">{t("who")}</th>
                <th scope="col">{t("direction")}</th>
                <th scope="col" className={s.num}>
                  {t("original")}
                </th>
                <th scope="col" className={s.num}>
                  {t("repaid")}
                </th>
                <th scope="col" className={s.num}>
                  {t("remaining")}
                </th>
              </tr>
            </thead>
            <tbody>
              {report.debts.map((d) => (
                <tr key={d.id}>
                  <th scope="row" dir="auto">
                    {d.counterparty}
                  </th>
                  <td>{t(`directions.${d.direction}`)}</td>
                  <td className={s.num}>
                    <Money value={d.original} />
                  </td>
                  <td className={s.num}>
                    <Money value={d.repaid_in_period} />
                  </td>
                  <td className={cn(s.num, !isNegative(d.remaining) && s.strong)}>
                    <Money value={d.remaining} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
