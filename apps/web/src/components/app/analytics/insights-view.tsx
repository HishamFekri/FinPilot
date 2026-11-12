"use client";

import { ChartColumn, FileText } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { type AllocationView, type Insights, insightsApi, type Totals } from "@/lib/analytics";
import { type GroupTotal, groupsApi } from "@/lib/everyday";
import { useGroupName, useGroups } from "../finance/category-groups";
import { localeTag } from "@/lib/format";
import { isNegative } from "@/lib/money";
import { cn } from "@/lib/utils";
import { Money, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { buttonClass, Notice, Spinner } from "../ui";
import s from "./analytics.module.css";
import {
  CategoryBars,
  CategoryTable,
  ChartCard,
  IncomeSpendingBars,
  SeriesTable,
  SpendingLine,
  useBucketLabel,
} from "./charts";
import {
  AccountSelect,
  AllocationSelect,
  CurrencySelect,
  customInvalid,
  type Period,
  PeriodPicker,
  periodQuery,
} from "./filters";

/**
 * Insights: where the money went, from the ledger (FINANCIAL_RULES.md §16). Spending is posted
 * expenses only; transfers, opening balances, adjustments and debt principal never count. One
 * currency at a time, never converted or added together.
 */
/** `embedded`: a tab of the Analytics page, so its title is a section heading, not the page's. */
export function InsightsView({ embedded = false }: { embedded?: boolean } = {}) {
  return (
    <FinanceGate>
      <InsightsPage embedded={embedded} />
    </FinanceGate>
  );
}

function InsightsPage({ embedded }: { embedded: boolean }) {
  const t = useTranslations("app.insights");
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const today = todayIn(user.time_zone);
  const [period, setPeriod] = useState<Period>({ key: "month", from: "", to: "" });
  const [currency, setCurrency] = useState("");
  const [accountId, setAccountId] = useState("");
  const [allocation, setAllocation] = useState<AllocationView>("include");
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{ for: string; value?: Insights; failed?: boolean } | null>(
    null,
  );
  const range = periodQuery(period, today);
  const invalid = customInvalid(period);
  const request = JSON.stringify([range, currency, accountId, allocation, revision, attempt]);

  useEffect(() => {
    if (invalid) return;
    let cancelled = false;
    insightsApi
      .get({
        ...range,
        currency: currency || undefined,
        account_id: accountId || undefined,
        trip_allocation: allocation === "include" ? undefined : allocation,
      })
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
        <Link href="/app/analytics?tab=reports" className={buttonClass("secondary")}>
          <FileText aria-hidden="true" />
          {t("toReports")}
        </Link>
      </div>

      <div className={cn(s.filterBar, s.noPrint)}>
        <PeriodPicker value={period} onChange={setPeriod} today={today} />
        <CurrencySelect
          value={currency || value?.currency || ""}
          onChange={setCurrency}
          codes={value?.currencies.length ? value.currencies : currency ? [currency] : []}
        />
        <AccountSelect value={accountId} onChange={setAccountId} />
        <AllocationSelect value={allocation} onChange={setAllocation} />
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
        <p className={appStyles.muted} role="status">
          <Spinner /> <span>{t("loading")}</span>
        </p>
      ) : !value.currency || !value.totals ? (
        <div className={s.empty}>
          <ChartColumn aria-hidden="true" />
          <p className={s.emptyTitle}>{t("emptyTitle")}</p>
          <p className={cn(appStyles.muted, appStyles.small)}>{t("emptyBody")}</p>
        </div>
      ) : (
        <div
          className={cn(s.page, stale && fs.refreshing)}
          aria-busy={stale || undefined}
          aria-live="polite"
        >
          <RangeLine value={value} />
          <Kpis totals={value.totals} previous={value.previous} />
          <TripSplit totals={value.totals} />
          <GroupBreakdown
            from={value.date_from}
            to={value.date_to}
            currency={value.currency}
            allocation={allocation}
          />
          {value.currencies.length > 1 ? <p className={s.note}>{t("separate")}</p> : null}
          <div className={s.grid2}>
            <ChartCard
              id="categories"
              title={t("byCategory")}
              chart={<CategoryBars items={value.categories} />}
              table={<CategoryTable items={value.categories} caption={t("byCategory")} />}
            />
            <ChartCard
              id="monthly"
              title={t("monthly")}
              note={<p className={cn(s.note, s.gapBelowSm)}>{t("monthlyNote")}</p>}
              chart={
                <IncomeSpendingBars
                  points={value.monthly}
                  grain="month"
                  label={t("monthlyAria", { currency: value.currency })}
                />
              }
              table={<SeriesTable points={value.monthly} grain="month" caption={t("monthly")} />}
            />
          </div>
          <ChartCard
            id="daily"
            title={t(`trend.${value.grain}`)}
            chart={
              <SpendingLine
                points={value.daily}
                grain={value.grain}
                label={t("trendAria", { currency: value.currency })}
              />
            }
            table={
              <SeriesTable
                points={value.daily}
                grain={value.grain}
                caption={t(`trend.${value.grain}`)}
                spendingOnly
              />
            }
          />
          <p className={s.note}>{t("rules")}</p>
        </div>
      )}
    </div>
  );
}

function RangeLine({ value }: { value: Insights }) {
  const t = useTranslations("app.insights");
  const label = useBucketLabel();
  return (
    <p className={s.reportMeta}>
      <span>
        {t("range", {
          from: label(value.date_from, "day", "long"),
          to: label(value.date_to, "day", "long"),
        })}
      </span>
      <span>
        <bdi dir="ltr">{value.currency}</bdi>
      </span>
    </p>
  );
}

export function Kpis({
  totals,
  previous,
  count = "entries",
}: {
  totals: Totals;
  previous: Totals | null;
  /** Insights count income and expense entries (as Home does); Reports count every transaction. */
  count?: "entries" | "transactions";
}) {
  const t = useTranslations("app.insights.kpi");
  const locale = useLocale() as Locale;
  return (
    <dl className={s.kpis}>
      <div className={s.kpi}>
        <dt>{t("spending")}</dt>
        <dd>
          <Money value={totals.expenses} className={s.kpiValue} />
          {previous ? (
            <span className={s.kpiDelta}>
              {t.rich("previous", { amount: () => <Money value={previous.expenses} /> })}
            </span>
          ) : null}
        </dd>
      </div>
      <div className={s.kpi}>
        <dt>{t("income")}</dt>
        <dd>
          <Money value={totals.income} className={cn(s.kpiValue, s.pos)} />
          {previous ? (
            <span className={s.kpiDelta}>
              {t.rich("previous", { amount: () => <Money value={previous.income} /> })}
            </span>
          ) : null}
        </dd>
      </div>
      <div className={s.kpi}>
        <dt>{t("net")}</dt>
        <dd>
          <Money
            value={totals.net}
            signed
            className={cn(s.kpiValue, isNegative(totals.net) ? s.neg : s.pos)}
          />
          <span className={s.kpiDelta}>{t("netHint")}</span>
        </dd>
      </div>
      <div className={s.kpi}>
        <dt>{t(count)}</dt>
        <dd>
          <span className={s.kpiValue}>
            {new Intl.NumberFormat(localeTag(locale)).format(totals.transactions)}
          </span>
          <span className={s.kpiDelta}>{t(`${count}Hint`)}</span>
          {previous ? (
            <span className={s.kpiDelta}>
              {t("previousCount", {
                count: previous.transactions,
              })}
            </span>
          ) : null}
        </dd>
      </div>
    </dl>
  );
}

/** Spending's two parts (§11.7): everyday spending and allocated trip shares. */
function TripSplit({ totals }: { totals: Totals }) {
  const t = useTranslations("app.insights");
  const shares = totals.trip_shares;
  if (!shares || /^0(\.0+)?$/.test(shares.amount)) return null;
  return (
    <p className={s.note}>
      {t.rich("tripSplit", {
        everyday: () => <Money value={totals.non_trip_expenses ?? shares} />,
        shares: () => <Money value={shares} />,
      })}
    </p>
  );
}

/**
 * Spending by category group: the same category totals as the chart below, regrouped. Each
 * category is in at most one group, so the groups add up to the spending above.
 */
function GroupBreakdown({
  from,
  to,
  currency,
  allocation,
}: {
  from: string;
  to: string;
  currency: string;
  allocation: AllocationView;
}) {
  const t = useTranslations("app.insights.groups");
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const groupName = useGroupName();
  const groups = useGroups(revision);
  const request = JSON.stringify([from, to, currency, allocation, revision]);
  const [rows, setRows] = useState<{ for: string; list?: GroupTotal[] } | null>(null);
  useEffect(() => {
    let cancelled = false;
    groupsApi
      .totals({ from, to, currency, trip_allocation: allocation })
      .then((list) => {
        if (!cancelled) setRows({ for: request, list });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setRows({ for: request, list: [] });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, handleAuthError]);
  const list = rows?.list ?? [];
  const positive = list.filter((r) => Number(r.amount.amount) > 0);
  if (positive.length === 0) return null;
  const peak = Math.max(...positive.map((r) => Number(r.amount.amount)));
  return (
    <section className={s.groupCard} aria-labelledby="groups-title">
      <h2 id="groups-title" className={s.groupCardTitle}>
        {t("title")}
      </h2>
      <table className={s.groupTable}>
        <caption className={appStyles.srOnly}>{t("title")}</caption>
        <thead className={appStyles.srOnly}>
          <tr>
            <th scope="col">{t("group")}</th>
            <th scope="col">{t("amount")}</th>
            <th scope="col">{t("share")}</th>
          </tr>
        </thead>
        <tbody>
          {positive.map((r) => (
            <tr key={r.group_id ?? "none"}>
              <th scope="row">
                <span dir="auto">
                  {r.group_id
                    ? groupName(
                        (groups ?? []).find((g) => g.id === r.group_id),
                        t("archived"),
                      )
                    : t("ungrouped")}
                </span>
                <span className={s.groupBar} aria-hidden="true">
                  <span style={{ inlineSize: `${(Number(r.amount.amount) / peak) * 100}%` }} />
                </span>
              </th>
              <td className={s.num}>
                <Money value={r.amount} />
              </td>
              <td className={s.num}>
                <bdi dir="ltr">{`${r.percent}%`}</bdi>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={s.note}>{t("note")}</p>
    </section>
  );
}
