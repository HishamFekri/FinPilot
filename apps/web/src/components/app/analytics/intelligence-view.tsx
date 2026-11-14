"use client";

import { ChevronDown, ChevronLeft, ChevronRight, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useId, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import {
  addMonths,
  type AllocationView,
  type Comparison,
  type Intelligence,
  type IntelligenceHighlight,
  type IntelligenceInsight,
  intelligenceApi,
  type IntelligencePeriod,
  type SeriesPoint,
} from "@/lib/analytics";
import { addAmounts } from "@/lib/decimal";
import type { Category } from "@/lib/finance";
import { formatDate, localeTag } from "@/lib/format";
import { formatMoney, type Money as MoneyValue } from "@/lib/money";
import { cn } from "@/lib/utils";
import { Money, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import { useCategoryName } from "../finance/parts";
import { Select } from "../select";
import { Loading, Notice } from "../ui";
import { usePercent } from "./budgets-view";
import {
  CategoryBars,
  CategoryTable,
  ChartCard,
  IncomeSpendingBars,
  SeriesTable,
  SpendingLine,
} from "./charts";
import { AccountSelect, AllocationSelect, CurrencySelect } from "./filters";
import {
  BudgetBullets,
  CategoryHeatmap,
  StackedMonths,
  StackedTable,
  ValueColumns,
  ValueTable,
} from "./intelligence-charts";
import s from "./intelligence.module.css";

const PERIODS: IntelligencePeriod[] = ["mtd", "qtd", "half", "ytd", "rolling6", "last12", "custom"];
const EQUIVALENTS = ["EUR", "USD", "TRY", "GBP"];

/**
 * Spending Intelligence: how spending is changing and why, from the same ledger figures as Reports
 * (FINANCIAL_RULES.md §16.6). Exact per currency. The one projection is labelled an estimate.
 */
export function IntelligenceView() {
  const t = useTranslations("app.intelligence");
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const today = todayIn(user.time_zone);
  const [period, setPeriod] = useState<IntelligencePeriod>("mtd");
  const [asOf, setAsOf] = useState(today);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [currency, setCurrency] = useState("");
  const [accountId, setAccountId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [type, setType] = useState<"" | "income" | "expense">("");
  const [allocation, setAllocation] = useState<AllocationView>("include");
  const [equivalent, setEquivalent] = useState("");
  const [trendEnd, setTrendEnd] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{
    for: string;
    value?: Intelligence;
    failed?: boolean;
  } | null>(null);

  const customReady = period !== "custom" || (Boolean(from && to) && from <= to);
  const params = {
    period,
    as_of: period === "custom" || asOf === today ? undefined : asOf,
    from: period === "custom" ? from : undefined,
    to: period === "custom" ? to : undefined,
    currency: currency || undefined,
    account_id: accountId || undefined,
    category_id: categoryId || undefined,
    type: type || undefined,
    trip_allocation: allocation === "include" ? undefined : allocation,
    equivalent: equivalent || undefined,
    trend_end: trendEnd ?? undefined,
  };
  const request = JSON.stringify([params, revision, attempt]);

  useEffect(() => {
    if (!customReady) return;
    let cancelled = false;
    intelligenceApi
      .get(params)
      .then((value) => {
        if (!cancelled) setData({ for: request, value });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setData({ for: request, failed: true });
      });
    return () => {
      cancelled = true;
    };
    // `request` says when to run it again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, customReady, handleAuthError]);

  const value = data?.value;
  const stale = data !== null && data.for !== request;

  return (
    <div className={s.root}>
      <div>
        <h2 className={s.pageHeading}>{t("title")}</h2>
        <p className={appStyles.pageLead}>{t("lead")}</p>
      </div>

      <div className={s.filters}>
        <div className={s.periods} role="group" aria-label={t("filters.period")}>
          {PERIODS.map((key) => (
            <button
              key={key}
              type="button"
              className={s.period}
              aria-pressed={period === key}
              onClick={() => {
                setPeriod(key);
                setTrendEnd(null);
              }}
            >
              {t(`periods.${key}`)}
            </button>
          ))}
        </div>
        <div className={s.filterRow}>
          {period === "custom" ? (
            <>
              <DateField label={t("filters.from")} value={from} max={today} onChange={setFrom} />
              <DateField label={t("filters.to")} value={to} max={today} onChange={setTo} />
            </>
          ) : (
            <DateField
              label={t("filters.asOf")}
              value={asOf}
              max={today}
              onChange={(next) => {
                setAsOf(next || today);
                setTrendEnd(null);
              }}
            />
          )}
          <CurrencySelect
            value={currency || value?.currency || ""}
            onChange={setCurrency}
            codes={value?.currencies.length ? value.currencies : currency ? [currency] : []}
          />
          <AccountSelect value={accountId} onChange={setAccountId} />
          <CategoryFilter value={categoryId} onChange={setCategoryId} />
          <TypeFilter value={type} onChange={setType} />
          <AllocationSelect value={allocation} onChange={setAllocation} />
          <EquivalentFilter value={equivalent} onChange={setEquivalent} />
        </div>
        {period === "custom" && from && to && from > to ? (
          <p className={appStyles.error} role="alert">
            {t("filters.invalid")}
          </p>
        ) : null}
      </div>

      {data?.failed ? (
        <Notice tone="error">
          {t("failed")}{" "}
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
      ) : value.empty || !value.totals ? (
        <div className={s.empty}>
          <p className={s.emptyTitle}>{t("empty.title")}</p>
          <p className={s.muted}>{t("empty.body")}</p>
        </div>
      ) : (
        <div className={s.report} data-stale={stale || undefined} aria-busy={stale || undefined}>
          <Report
            value={value}
            onTrend={(month) => setTrendEnd(month)}
            lastMonth={(value.as_of ?? today).slice(0, 7)}
          />
        </div>
      )}
    </div>
  );
}

function DateField({
  label,
  value,
  max,
  onChange,
}: {
  label: string;
  value: string;
  max: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className={s.field}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="date"
        className={appStyles.input}
        value={value}
        max={max}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function CategoryFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const t = useTranslations("app.intelligence.filters");
  const { categories } = useFinance();
  const name = useCategoryName();
  const id = useId();
  return (
    <div className={s.field}>
      <label htmlFor={id}>{t("category")}</label>
      <Select id={id} value={value} onValueChange={onChange}>
        <option value="">{t("allCategories")}</option>
        {categories
          .filter((c: Category) => c.kind === "expense")
          .map((c: Category) => (
            <option key={c.id} value={c.id}>
              {name(c)}
            </option>
          ))}
      </Select>
    </div>
  );
}

function TypeFilter({
  value,
  onChange,
}: {
  value: "" | "income" | "expense";
  onChange: (value: "" | "income" | "expense") => void;
}) {
  const t = useTranslations("app.intelligence.filters");
  const id = useId();
  return (
    <div className={s.field}>
      <label htmlFor={id}>{t("type")}</label>
      <Select
        id={id}
        value={value}
        onValueChange={(next) => onChange(next as "" | "income" | "expense")}
      >
        <option value="">{t("allTypes")}</option>
        <option value="expense">{t("expense")}</option>
        <option value="income">{t("income")}</option>
      </Select>
    </div>
  );
}

function EquivalentFilter({
  value,
  onChange,
}: {
  value: string;
  onChange: (code: string) => void;
}) {
  const t = useTranslations("app.intelligence.filters");
  const id = useId();
  return (
    <div className={s.field}>
      <label htmlFor={id}>{t("equivalent")}</label>
      <Select id={id} value={value} onValueChange={onChange}>
        <option value="">{t("noEquivalent")}</option>
        {EQUIVALENTS.map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </Select>
    </div>
  );
}

function useRange() {
  const locale = useLocale() as Locale;
  const day = (d: string) => formatDate(`${d}T12:00:00Z`, locale, "UTC");
  return (w: { from: string; to: string }) => `${day(w.from)} – ${day(w.to)}`;
}

function Section({
  id,
  title,
  lead,
  children,
}: {
  id: string;
  title: string;
  lead?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className={s.section} aria-labelledby={`${id}-title`}>
      <div>
        <h3 id={`${id}-title`} className={s.sectionTitle}>
          {title}
        </h3>
        {lead ? <p className={s.sectionLead}>{lead}</p> : null}
      </div>
      {children}
    </section>
  );
}

function Stat({
  label,
  children,
  sub,
  tone,
}: {
  label: string;
  children: ReactNode;
  sub?: ReactNode;
  tone?: "in" | "out";
}) {
  return (
    <div className={s.stat}>
      <dt>{label}</dt>
      <dd data-tone={tone}>{children}</dd>
      {sub ? <span className={s.statSub}>{sub}</span> : null}
    </div>
  );
}

function transactionsHref(
  category: Category | null | undefined,
  window: { from: string; to: string },
  currency: string,
) {
  const query = new URLSearchParams({ from: window.from, to: window.to, currency });
  if (category) query.set("category", category.id);
  return `/app/transactions?${query.toString()}`;
}

function Report({
  value,
  onTrend,
  lastMonth,
}: {
  value: Intelligence;
  onTrend: (month: string) => void;
  lastMonth: string;
}) {
  const t = useTranslations("app.intelligence");
  const locale = useLocale() as Locale;
  const range = useRange();
  const percent = usePercent();
  const code = value.currency ?? "";
  const months = value.months ?? [];
  const points: SeriesPoint[] = months.map((m) => ({
    bucket: m.month,
    income: m.income,
    expenses: m.spent,
  }));
  const partial = months.some((m) => m.partial);
  const trendMonth = value.trend_end.slice(0, 7);
  const weekdayName = new Intl.DateTimeFormat(localeTag(locale), {
    weekday: "long",
    timeZone: "UTC",
  });
  // 5 January 2026 is a Monday, and the API counts weekdays from Monday.
  const weekday = (i: number) => weekdayName.format(new Date(Date.UTC(2026, 0, 5 + i, 12)));
  const weekdayShort = new Intl.DateTimeFormat(localeTag(locale), {
    weekday: "short",
    timeZone: "UTC",
  });
  const weekItems = (value.weekdays ?? []).map((w) => ({
    key: String(w.weekday),
    label: weekdayShort.format(new Date(Date.UTC(2026, 0, 5 + w.weekday, 12))),
    long: weekday(w.weekday),
    value: w.average,
  }));
  const dayItems = (value.daily ?? []).map((d) => ({
    key: d.day,
    label: String(Number(d.day.slice(8))),
    long: formatDate(`${d.day}T12:00:00Z`, locale, "UTC"),
    value: d.amount,
  }));

  return (
    <>
      {value.notes.length > 0 ? (
        <ul className={s.notes}>
          {value.notes.map((note) => (
            <li key={note}>
              <TriangleAlert aria-hidden="true" />
              {t(`notes.${note}`)}
            </li>
          ))}
        </ul>
      ) : null}

      <p className={s.periodLine}>
        {t("periodLine", {
          current: range(value.window),
          previous: range(value.previous),
        })}
      </p>

      <Insights value={value} />
      <Pace value={value} />

      <Section id="intel-compare" title={t("compare.title")} lead={t("compare.lead")}>
        <Comparisons items={value.comparisons ?? []} />
      </Section>

      {value.equivalent ? <Equivalent value={value} /> : null}

      <div className={s.grid2}>
        <ChartCard
          id="intel-trend"
          title={t("charts.trend")}
          note={partial ? <p className={s.chartNote}>{t("charts.partial")}</p> : undefined}
          extra={
            <span className={s.stepper}>
              <button
                type="button"
                aria-label={t("charts.earlier")}
                onClick={() => onTrend(addMonths(trendMonth, -12))}
              >
                <ChevronLeft aria-hidden="true" className={s.flip} />
              </button>
              <button
                type="button"
                aria-label={t("charts.later")}
                disabled={trendMonth >= lastMonth}
                onClick={() => onTrend(addMonths(trendMonth, 12))}
              >
                <ChevronRight aria-hidden="true" className={s.flip} />
              </button>
            </span>
          }
          chart={<SpendingLine points={points} grain="month" label={t("charts.trend")} />}
          table={<SeriesTable points={points} grain="month" caption={t("charts.trend")} />}
        />
        <ChartCard
          id="intel-income"
          title={t("charts.income")}
          chart={<IncomeSpendingBars points={points} grain="month" label={t("charts.income")} />}
          table={<SeriesTable points={points} grain="month" caption={t("charts.income")} />}
        />
        <ChartCard
          id="intel-categories"
          title={t("charts.categories")}
          chart={<CategoryBars items={value.categories ?? []} limit={8} />}
          table={<CategoryTable items={value.categories ?? []} caption={t("charts.categories")} />}
        />
        <section className={s.card} aria-labelledby="intel-heat-title">
          <h4 id="intel-heat-title" className={s.cardTitle}>
            {t("charts.changes")}
          </h4>
          <p className={s.chartNote}>{t("charts.changesNote")}</p>
          {value.heatmap && value.heatmap.rows.length > 0 ? (
            <CategoryHeatmap data={value.heatmap} />
          ) : (
            <p className={s.muted}>{t("charts.noData")}</p>
          )}
        </section>
        <ChartCard
          id="intel-weekdays"
          title={t("charts.weekdays")}
          note={<p className={s.chartNote}>{t("charts.weekdaysNote")}</p>}
          chart={
            <ValueColumns
              items={weekItems}
              label={t("charts.weekdays")}
              hint={t("charts.inspect")}
            />
          }
          table={
            <ValueTable
              items={weekItems}
              caption={t("charts.weekdays")}
              head={[t("charts.day"), t("charts.average")]}
            />
          }
        />
        {dayItems.length > 0 ? (
          <ChartCard
            id="intel-daily"
            title={t("charts.daily")}
            chart={
              <ValueColumns items={dayItems} label={t("charts.daily")} hint={t("charts.inspect")} />
            }
            table={
              <ValueTable
                items={dayItems}
                caption={t("charts.daily")}
                head={[t("charts.day"), t("charts.amount")]}
              />
            }
          />
        ) : null}
      </div>

      <Section id="intel-highlights" title={t("highlights.title")} lead={t("highlights.lead")}>
        <Highlights value={value} />
      </Section>

      <Section id="intel-fixed" title={t("fixed.title")} lead={t("fixed.lead")}>
        {value.fixed ? (
          <dl className={s.stats}>
            <Stat
              label={t("fixed.scheduled")}
              sub={t("fixed.scheduledNote", {
                amount: formatMoney(value.fixed.subscriptions, locale),
              })}
            >
              <Money
                value={{
                  amount: addAmounts([
                    value.fixed.scheduled.amount,
                    value.fixed.subscriptions.amount,
                  ]),
                  currency: code,
                }}
              />
            </Stat>
            <Stat label={t("fixed.variable")}>
              <Money value={value.fixed.variable} />
            </Stat>
            <Stat label={t("fixed.financed")} sub={t("fixed.financedNote")}>
              <Money value={value.fixed.financed} />
            </Stat>
            <Stat label={t("fixed.trip")}>
              <Money value={value.fixed.trip} />
            </Stat>
          </dl>
        ) : null}
        <ChartCard
          id="intel-fixed-chart"
          title={t("fixed.chart")}
          chart={<StackedMonths months={months} label={t("fixed.chart")} />}
          table={<StackedTable months={months} caption={t("fixed.chart")} />}
        />
      </Section>

      <Section id="intel-budgets" title={t("budgets.title")} lead={t("budgets.lead")}>
        {(value.budgets ?? []).length > 0 ? (
          <BudgetBullets budgets={value.budgets ?? []} />
        ) : (
          <p className={s.muted}>
            {t("budgets.none")}{" "}
            <Link href="/app/budgets" className={appStyles.linkBtn}>
              {t("budgets.create")}
            </Link>
          </p>
        )}
      </Section>

      {value.commitments ? <Commitments value={value} /> : null}
      {value.trips ? <Trips value={value} /> : null}

      <details className={s.method}>
        <summary>{t("method.title")}</summary>
        <ul>
          {(["spending", "comparable", "percent", "pace", "fixed", "currency"] as const).map(
            (key) => (
              <li key={key}>{t(`method.${key}`)}</li>
            ),
          )}
        </ul>
        <p>{t("method.detail", { share: percent("2") })}</p>
      </details>
    </>
  );
}

function useChange() {
  const percent = usePercent();
  return (value: string | null) =>
    value === null ? "" : `${Number(value) > 0 ? "+" : ""}${percent(value)}%`;
}

function Comparisons({ items }: { items: Comparison[] }) {
  const t = useTranslations("app.intelligence.compare");
  const range = useRange();
  const change = useChange();
  return (
    <div className={s.tableWrap}>
      <table className={s.compare}>
        <caption className={s.srOnly}>{t("title")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("period")}</th>
            <th scope="col">{t("current")}</th>
            <th scope="col">{t("previous")}</th>
            <th scope="col">{t("difference")}</th>
            <th scope="col">{t("change")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.key}>
              <th scope="row" data-label={t("period")}>
                {t(`keys.${c.key}`)}
                <span className={s.compareHint}>{t(`hints.${c.key}`)}</span>
              </th>
              <td data-label={t("current")}>
                <Money value={c.current_amount} />
                <span className={s.compareHint}>{range(c.current)}</span>
              </td>
              <td data-label={t("previous")}>
                <Money value={c.previous_amount} />
                <span className={s.compareHint}>{range(c.previous)}</span>
              </td>
              <td data-label={t("difference")}>
                <span className={s.plain}>
                  <Money value={c.difference} signed />
                </span>
              </td>
              <td data-label={t("change")}>
                {c.percent !== null ? (
                  <bdi dir="ltr">{change(c.percent)}</bdi>
                ) : (
                  <span className={s.compareHint}>{t(`reasons.${c.reason ?? "no_history"}`)}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Insights({ value }: { value: Intelligence }) {
  const t = useTranslations("app.intelligence.insights");
  const items = value.insights ?? [];
  return (
    <Section id="intel-insights" title={t("title")} lead={t("lead")}>
      {items.length === 0 ? (
        <p className={s.muted}>{t("none")}</p>
      ) : (
        <ol className={s.insights}>
          {items.map((item) => (
            <InsightRow key={item.code} item={item} value={value} />
          ))}
        </ol>
      )}
    </Section>
  );
}

function insightHref(item: IntelligenceInsight, value: Intelligence): string {
  switch (item.kind) {
    case "category":
      return transactionsHref(item.category, value.window, value.currency ?? "");
    case "budget":
      return "/app/budgets";
    case "share":
      return "/app/subscriptions";
    case "commitments":
      return "/app/recurring";
    default:
      return transactionsHref(null, value.window, value.currency ?? "");
  }
}

function InsightRow({ item, value }: { item: IntelligenceInsight; value: Intelligence }) {
  const t = useTranslations("app.intelligence.insights");
  const locale = useLocale() as Locale;
  const name = useCategoryName();
  const percent = usePercent();
  const range = useRange();
  const id = useId();
  const [open, setOpen] = useState(false);
  const money = (m?: MoneyValue | null) => (m ? formatMoney(m, locale) : "");
  const params = {
    category: item.category ? name(item.category) : t("overallBudget"),
    current: money(item.current),
    previous: money(item.previous),
    difference: money(item.difference),
    limit: money(item.limit),
    total: money(item.total),
    percent: item.percent ? percent(item.percent) : "",
    months: item.months ?? 0,
    days: item.days ?? 0,
  };
  return (
    <li className={s.insight} data-open={open || undefined}>
      <button
        type="button"
        className={s.insightHead}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        <span>{t(`statements.${item.code}`, params)}</span>
        <ChevronDown aria-hidden="true" />
      </button>
      <div id={id} className={s.insightBody}>
        <div>
          <p>{t(`support.${item.code}`, params)}</p>
          <p className={s.compareHint}>{t("period", { range: range(value.window) })}</p>
          <Link href={insightHref(item, value)} className={s.insightLink}>
            {t("detail")}
            <ChevronRight aria-hidden="true" className={s.flip} />
          </Link>
        </div>
      </div>
    </li>
  );
}

function Pace({ value }: { value: Intelligence }) {
  const t = useTranslations("app.intelligence.pace");
  const percent = usePercent();
  const pace = value.pace;
  if (!pace) return null;
  return (
    <Section id="intel-pace" title={t("title")} lead={t("lead", { days: pace.days_elapsed })}>
      <dl className={s.stats}>
        <Stat label={t("soFar")}>
          <Money value={pace.so_far} />
        </Stat>
        <Stat label={t("average")} sub={t("perDay")}>
          <Money value={pace.average_daily} />
        </Stat>
        <Stat
          label={t("normal")}
          sub={
            pace.enough_history
              ? t("normalNote", { months: pace.months_used, days: pace.days_elapsed })
              : t("notEnough")
          }
        >
          {pace.historical_daily ? <Money value={pace.historical_daily} /> : "—"}
        </Stat>
        <Stat
          label={t("difference")}
          sub={
            pace.percent !== null
              ? `${Number(pace.percent) > 0 ? "+" : ""}${percent(pace.percent)}%`
              : undefined
          }
        >
          {pace.difference_daily ? (
            <span className={s.plain}>
              <Money value={pace.difference_daily} signed />
            </span>
          ) : (
            "—"
          )}
        </Stat>
        <Stat
          label={t("projected")}
          sub={
            pace.projected
              ? t("projectedNote")
              : t(pace.projection_reason === "month_over" ? "monthOver" : "tooEarly")
          }
        >
          {pace.projected ? (
            <span className={s.estimate}>
              <span>{"≈ "}</span>
              <Money value={pace.projected} />
              <em>{t("estimate")}</em>
            </span>
          ) : (
            "—"
          )}
        </Stat>
      </dl>
    </Section>
  );
}

function Highlight({
  label,
  row,
  statement,
  value,
}: {
  label: string;
  row: IntelligenceHighlight | null | undefined;
  statement?: (row: IntelligenceHighlight, name: string) => string;
  value: Intelligence;
}) {
  const t = useTranslations("app.intelligence.highlights");
  const name = useCategoryName();
  if (!row) return null;
  const title = row.category ? name(row.category) : t("uncategorized");
  return (
    <li className={s.highlight}>
      <span className={s.highlightLabel}>{label}</span>
      <strong>
        <bdi>{title}</bdi>
      </strong>
      {statement ? <span className={s.highlightText}>{statement(row, title)}</span> : null}
      <span className={s.compareHint}>
        <Money value={row.current} /> {t("vs")} <Money value={row.previous} />
      </span>
      <Link
        href={transactionsHref(row.category, value.window, value.currency ?? "")}
        className={s.insightLink}
      >
        {t("open")}
        <ChevronRight aria-hidden="true" className={s.flip} />
      </Link>
    </li>
  );
}

function Highlights({ value }: { value: Intelligence }) {
  const t = useTranslations("app.intelligence.highlights");
  const locale = useLocale() as Locale;
  const percent = usePercent();
  const name = useCategoryName();
  const marks = value.highlights;
  if (!marks) return null;
  const change = (m: MoneyValue) =>
    formatMoney({ ...m, amount: m.amount.replace("-", "") }, locale);
  const nothing =
    !marks.highest &&
    !marks.fastest &&
    !marks.largest_increase &&
    !marks.largest_decrease &&
    marks.new.length === 0;
  if (nothing) return <p className={s.muted}>{t("none")}</p>;
  return (
    <>
      <ul className={s.highlights}>
        <Highlight label={t("highest")} row={marks.highest} value={value} />
        <Highlight
          label={t("fastest")}
          row={marks.fastest}
          value={value}
          statement={(row, title) =>
            t("fastestText", { category: title, percent: percent(row.percent ?? "0") })
          }
        />
        <Highlight
          label={t("increase")}
          row={marks.largest_increase}
          value={value}
          statement={(row, title) =>
            t("increaseText", { category: title, amount: change(row.change) })
          }
        />
        <Highlight
          label={t("decrease")}
          row={marks.largest_decrease}
          value={value}
          statement={(row, title) =>
            t("decreaseText", { category: title, amount: change(row.change) })
          }
        />
      </ul>
      {marks.new.length > 0 ? (
        <p className={s.inlineList}>
          <strong>{t("new")}</strong>{" "}
          {marks.new.map((n, i) => (
            <span key={n.category?.id ?? i}>
              <Link
                href={transactionsHref(n.category, value.window, value.currency ?? "")}
                className={appStyles.linkBtn}
              >
                <bdi>{n.category ? name(n.category) : t("uncategorized")}</bdi>
              </Link>{" "}
              <Money value={n.current} />
              {i < marks.new.length - 1 ? " · " : ""}
            </span>
          ))}
        </p>
      ) : null}
      {marks.unusual.length > 0 ? (
        <ul className={s.unusual}>
          {marks.unusual.map((u, i) => (
            <li key={u.category?.id ?? i}>
              <TriangleAlert aria-hidden="true" />
              {t("unusualText", {
                category: u.category ? name(u.category) : t("uncategorized"),
                now: formatMoney(u.current, locale),
                usual: formatMoney(u.typical, locale),
                months: u.months,
              })}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

function Commitments({ value }: { value: Intelligence }) {
  const t = useTranslations("app.intelligence.commitments");
  const percent = usePercent();
  const c = value.commitments;
  if (!c) return null;
  return (
    <Section id="intel-commitments" title={t("title")} lead={t("lead")}>
      <h4 className={s.groupTitle}>{t("actual")}</h4>
      <dl className={s.stats}>
        <Stat label={t("income")} tone="in">
          <Money value={c.income} />
        </Stat>
        <Stat label={t("spent")} tone="out">
          <Money value={c.spent} />
        </Stat>
        <Stat label={t("net")}>
          <Money value={c.net} signed />
        </Stat>
        <Stat label={t("confirmed")} sub={t("confirmedNote")}>
          <Money value={c.confirmed} />
        </Stat>
      </dl>
      <h4 className={s.groupTitle}>{t("scheduled", { days: c.upcoming_days })}</h4>
      <dl className={s.stats}>
        <Stat label={t("upcomingRecurring")}>
          <Money value={c.upcoming_recurring} />
        </Stat>
        <Stat label={t("upcomingInstallments")}>
          <Money value={c.upcoming_installments} />
        </Stat>
        <Stat
          label={t("ratio")}
          sub={
            c.installment_income_ratio
              ? t("ratioNote", {
                  months: c.income_months,
                })
              : t("ratioNone")
          }
        >
          {c.installment_income_ratio ? `${percent(c.installment_income_ratio)}%` : "—"}
        </Stat>
        <Stat label={t("remaining")} sub={t("remainingNote")}>
          <span className={s.estimate}>
            <span>{"≈ "}</span>
            <Money value={c.remaining_after_known} />
          </span>
        </Stat>
      </dl>
      {c.upcoming_unknown > 0 ? (
        <p className={s.chartNote}>{t("unknown", { count: c.upcoming_unknown })}</p>
      ) : null}
    </Section>
  );
}

function Trips({ value }: { value: Intelligence }) {
  const t = useTranslations("app.intelligence.trips");
  const percent = usePercent();
  const trips = value.trips;
  if (!trips) return null;
  return (
    <Section id="intel-trips" title={t("title")} lead={t("lead")}>
      <dl className={s.stats}>
        <Stat label={t("everyday")}>
          <Money value={trips.everyday} />
        </Stat>
        <Stat
          label={t("shares")}
          sub={trips.percent ? t("share", { percent: percent(trips.percent) }) : undefined}
        >
          <Money value={trips.shares} />
        </Stat>
        {trips.cash_paid ? (
          <Stat label={t("cashPaid")} sub={t("cashNote")}>
            <Money value={trips.cash_paid} />
          </Stat>
        ) : null}
        {trips.cash_received ? (
          <Stat label={t("cashBack")}>
            <Money value={trips.cash_received} />
          </Stat>
        ) : null}
      </dl>
      {trips.by_trip.length > 0 ? (
        <ul className={s.tripList}>
          {trips.by_trip.map((trip) => (
            <li key={trip.id}>
              <Link href={`/app/trips/${trip.id}`} className={appStyles.linkBtn}>
                <bdi>{trip.name}</bdi>
              </Link>
              <Money value={trip.amount} />
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

function Equivalent({ value }: { value: Intelligence }) {
  const t = useTranslations("app.intelligence.equivalent");
  const locale = useLocale() as Locale;
  const percent = usePercent();
  const e = value.equivalent;
  if (!e) return null;
  return (
    <Section id="intel-equivalent" title={t("title", { currency: e.target })} lead={t("lead")}>
      {e.complete && e.current ? (
        <dl className={s.stats}>
          <Stat label={t("current")}>
            <span className={s.estimate}>
              <span>{"≈ "}</span>
              <Money value={e.current} />
            </span>
          </Stat>
          <Stat label={t("previous")}>
            {e.previous ? (
              <span className={s.estimate}>
                <span>{"≈ "}</span>
                <Money value={e.previous} />
              </span>
            ) : (
              "—"
            )}
          </Stat>
          <Stat label={t("change")}>
            {e.percent !== null ? `${Number(e.percent) > 0 ? "+" : ""}${percent(e.percent)}%` : "—"}
          </Stat>
        </dl>
      ) : (
        <p className={s.warn}>
          <TriangleAlert aria-hidden="true" />
          {t("incomplete")}
        </p>
      )}
      <ul className={cn(s.tripList, s.rateList)}>
        {e.parts.map((part) => (
          <li key={part.currency}>
            <span>
              <Money value={part.spent} display="code" />
            </span>
            <span className={s.compareHint}>
              {part.status === "unavailable"
                ? t("noRate")
                : part.status === "same"
                  ? t("same")
                  : t("rate", {
                      target: e.target,
                      rate: part.rate ?? "",
                      currency: part.currency,
                      source: part.provider ?? part.upstream ?? "",
                      date: part.rate_date
                        ? formatDate(`${part.rate_date}T12:00:00Z`, locale, "UTC")
                        : "",
                    })}
            </span>
          </li>
        ))}
      </ul>
      {e.is_stale ? <p className={s.chartNote}>{t("stale")}</p> : null}
    </Section>
  );
}
