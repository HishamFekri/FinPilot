"use client";

import { Tabs } from "@base-ui/react/tabs";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/routing";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import { budgetsApi } from "@/lib/analytics";
import { addAmounts, percentOf, subtractAmounts } from "@/lib/decimal";
import { financeApi } from "@/lib/finance";
import { formatDate } from "@/lib/format";
import { installmentsApi } from "@/lib/installments";
import { formatMoney, type Money as MoneyValue } from "@/lib/money";
import { recurringApi } from "@/lib/recurring";
import { tripsApi } from "@/lib/trips";
import { Money, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import { useAccountName, useCategoryName } from "../finance/parts";
import { InstallmentAnalytics } from "../installments/installment-analytics";
import { Notice, Spinner, Tag } from "../ui";
import { useLoad } from "../use-load";
import { useDisplayCurrency } from "../balances/display-currency";
import { TotalBalanceCard, useTotalBalance } from "../balances/total-balance";
import { InsightsView } from "./insights-view";
import { IntelligenceView } from "./intelligence-view";
import { ReportsView } from "./reports-view";
import s from "./analytics.module.css";

const TABS = [
  "overview",
  "intelligence",
  "spending",
  "reports",
  "accounts",
  "budgets",
  "installments",
  "trips",
] as const;
type Tab = (typeof TABS)[number];

export function AnalyticsView() {
  return (
    <FinanceGate>
      <AnalyticsPage />
    </FinanceGate>
  );
}

function AnalyticsPage() {
  const t = useTranslations("app.analytics");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const asked = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(asked ?? "") ? (asked as Tab) : "overview";

  return (
    <div className={s.page}>
      <header className={cn(s.head, s.noPrint)}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
      </header>
      <Tabs.Root
        className={s.tabsRoot}
        value={tab}
        onValueChange={(value) => {
          const next = new URLSearchParams(params.toString());
          next.set("tab", String(value));
          router.replace(`${pathname}?${next.toString()}`, { scroll: false });
        }}
      >
        <Tabs.List className={cn(s.tabs, s.noPrint)} aria-label={t("sections")}>
          {TABS.map((key) => (
            <Tabs.Tab key={key} value={key} className={s.tab}>
              {t(`tabs.${key}`)}
            </Tabs.Tab>
          ))}
          <Tabs.Indicator className={s.tabIndicator} />
        </Tabs.List>
        <Tabs.Panel value="overview" className={s.panel}>
          <Overview />
        </Tabs.Panel>
        <Tabs.Panel value="intelligence" className={s.panel}>
          <IntelligenceView />
        </Tabs.Panel>
        <Tabs.Panel value="spending" className={s.panel}>
          <InsightsView embedded />
        </Tabs.Panel>
        <Tabs.Panel value="reports" className={s.panel}>
          <ReportsView embedded />
        </Tabs.Panel>
        <Tabs.Panel value="accounts" className={s.panel}>
          <AccountsPanel />
        </Tabs.Panel>
        <Tabs.Panel value="budgets" className={s.panel}>
          <BudgetsPanel />
        </Tabs.Panel>
        <Tabs.Panel value="installments" className={s.panel}>
          <h2 className={s.sectionTitle}>{t("tabs.installments")}</h2>
          <InstallmentAnalytics />
        </Tabs.Panel>
        <Tabs.Panel value="trips" className={s.panel}>
          <TripsPanel />
        </Tabs.Panel>
      </Tabs.Root>
    </div>
  );
}

function cn(...names: (string | undefined | false)[]) {
  return names.filter(Boolean).join(" ");
}

function previousMonth(month: string): string {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const d = new Date(Date.UTC(y, m - 2, 1));
  return d.toISOString().slice(0, 7);
}

function Metric({
  label,
  help,
  children,
  sub,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className={s.metric}>
      <dt title={help}>{label}</dt>
      <dd>{children}</dd>
      {sub ? <span className={s.metricSub}>{sub}</span> : null}
    </div>
  );
}

/** The headline figures, each from the same API figures as its own page. Estimates say so. */
function Overview() {
  const t = useTranslations("app.analytics.overview");
  const locale = useLocale() as Locale;
  const user = useUser();
  const today = todayIn(user.time_zone);
  const month = today.slice(0, 7);
  const [target, setTarget] = useDisplayCurrency();
  const total = useTotalBalance(target);
  const thisMonth = useLoad(`summary:${month}`, () => financeApi.summary(month));
  const lastMonth = useLoad(`summary:${previousMonth(month)}`, () =>
    financeApi.summary(previousMonth(month)),
  );
  const plans = useLoad("installments-overview", installmentsApi.overview);
  const in30 = new Date(Date.parse(`${today}T12:00:00Z`) + 30 * 864e5).toISOString().slice(0, 10);
  const expected = useLoad(`expected:${today}`, () =>
    recurringApi.occurrences({ from: today, to: in30 }),
  );
  const budgets = useLoad(`budgets:${month}`, () => budgetsApi.month(month));

  const pct = (value: number | null) =>
    value === null
      ? "—"
      : new Intl.NumberFormat(locale, {
          style: "percent",
          maximumFractionDigits: 1,
          signDisplay: "auto",
        }).format(value / 100);

  // Expected over the next 30 days, per currency: recurring income in, recurring payments and
  // installments out. An estimate from schedules, never recorded money.
  const forecast = new Map<string, { inflow: string[]; outflow: string[] }>();
  const add = (m: MoneyValue, kind: "inflow" | "outflow") => {
    const entry = forecast.get(m.currency) ?? { inflow: [], outflow: [] };
    entry[kind].push(m.amount);
    forecast.set(m.currency, entry);
  };
  for (const o of expected.data ?? []) {
    if (o.status !== "pending" || !o.expected) continue;
    add(o.expected, o.kind === "income" ? "inflow" : "outflow");
  }
  for (const c of plans.data?.currencies ?? []) {
    const month1 = c.due_within.find((w) => w.months === 1);
    if (month1 && Number(month1.amount.amount) > 0) add(month1.amount, "outflow");
  }

  const exceeded = budgets.data?.budgets.filter((b) => b.state === "exceeded").length ?? 0;
  const approaching = budgets.data?.budgets.filter((b) => b.state === "approaching").length ?? 0;

  return (
    <div className={s.overview}>
      <h2 className={s.sectionTitle}>{t("title")}</h2>
      {thisMonth.error ? <Notice tone="error">{t("failed")}</Notice> : null}
      {!thisMonth.data ? (
        <p className={s.muted}>
          <Spinner /> {t("loading")}
        </p>
      ) : thisMonth.data.currencies.length === 0 ? (
        <p className={s.muted}>{t("nothingYet")}</p>
      ) : (
        thisMonth.data.currencies.map((c) => {
          const before = lastMonth.data?.currencies.find((x) => x.currency === c.currency);
          const saved = subtractAmounts(c.income.amount, c.expenses.amount);
          const rate = percentOf(saved, c.income.amount);
          const change =
            before && Number(before.expenses.amount) > 0
              ? percentOf(
                  subtractAmounts(c.expenses.amount, before.expenses.amount),
                  before.expenses.amount,
                )
              : null;
          return (
            <section key={c.currency} className={s.overviewCard} aria-label={c.currency}>
              <dl className={s.metrics}>
                <Metric label={t("income")}>
                  <Money value={c.income} />
                </Metric>
                <Metric label={t("spending")}>
                  <Money value={c.expenses} />
                </Metric>
                <Metric label={t("difference")} help={t("differenceHelp")}>
                  <Money value={{ amount: saved, currency: c.currency }} signed />
                </Metric>
                <Metric label={t("savingsRate")} help={t("savingsRateHelp")}>
                  {pct(rate)}
                </Metric>
                <Metric label={t("vsLastMonth")} help={t("vsLastMonthHelp")}>
                  {change === null ? "—" : pct(change)}
                </Metric>
              </dl>
            </section>
          );
        })
      )}

      <TotalBalanceCard
        target={target}
        onTarget={setTarget}
        data={total.data}
        failed={Boolean(total.error)}
        loading={total.loading}
        onRefresh={total.refresh}
      />

      <section className={s.overviewCard} aria-labelledby="overview-commitments">
        <h3 id="overview-commitments" className={s.subTitle}>
          {t("commitments")}
        </h3>
        {(plans.data?.currencies ?? []).length === 0 ? (
          <p className={s.muted}>{t("noInstallments")}</p>
        ) : (
          <dl className={s.metrics}>
            {plans.data!.currencies.map((c) => (
              <Metric
                key={c.currency}
                label={t("installmentsMonthly", { currency: c.currency })}
                help={t("ratioHelp")}
                sub={
                  c.income_ratio_percent !== null
                    ? t("ofIncome", { percent: pct(Number(c.income_ratio_percent)) })
                    : t("noIncome")
                }
              >
                <Money value={c.monthly} />
              </Metric>
            ))}
          </dl>
        )}
      </section>

      <section className={s.overviewCard} aria-labelledby="overview-forecast">
        <h3 id="overview-forecast" className={s.subTitle}>
          {t("forecast")} <Tag>{t("estimate")}</Tag>
        </h3>
        {forecast.size === 0 ? (
          <p className={s.muted}>{t("noForecast")}</p>
        ) : (
          <dl className={s.metrics}>
            {[...forecast.entries()].map(([currency, f]) => {
              const inflow = addAmounts(f.inflow);
              const outflow = addAmounts(f.outflow);
              return (
                <Metric
                  key={currency}
                  label={t("forecastNet", { currency })}
                  sub={t("forecastParts", {
                    inflow: formatMoney({ amount: inflow, currency }, locale),
                    outflow: formatMoney({ amount: outflow, currency }, locale),
                  })}
                >
                  <Money value={{ amount: subtractAmounts(inflow, outflow), currency }} signed />
                </Metric>
              );
            })}
          </dl>
        )}
        <p className={s.muted}>{t("forecastHelp")}</p>
      </section>

      <section className={s.overviewCard} aria-labelledby="overview-budgets">
        <h3 id="overview-budgets" className={s.subTitle}>
          {t("budgets")}
        </h3>
        {!budgets.data || budgets.data.budgets.length === 0 ? (
          <p className={s.muted}>{t("noBudgets")}</p>
        ) : (
          <p>
            {t("budgetState", {
              total: budgets.data.budgets.length,
              exceeded,
              approaching,
            })}{" "}
            <Link href="/app/budgets" className={s.inlineLink}>
              {t("toBudgets")}
            </Link>
          </p>
        )}
      </section>
    </div>
  );
}

function AccountsPanel() {
  const t = useTranslations("app.analytics.accounts");
  const [target, setTarget] = useDisplayCurrency();
  const total = useTotalBalance(target);
  const accountName = useAccountName();
  const { accounts } = useFinance();
  return (
    <div className={s.overview}>
      <h2 className={s.sectionTitle}>{t("title")}</h2>
      <TotalBalanceCard
        target={target}
        onTarget={setTarget}
        data={total.data}
        failed={Boolean(total.error)}
        loading={total.loading}
        onRefresh={total.refresh}
        accountCount={accounts.filter((a) => a.status === "active").length}
      />
      {total.data ? (
        <div className={s.tableWrap}>
          <table className={s.simpleTable}>
            <caption className={s.srOnly}>{t("caption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("account")}</th>
                <th scope="col">{t("balance")}</th>
                <th scope="col">{t("equivalent", { currency: target })}</th>
              </tr>
            </thead>
            <tbody>
              {total.data.accounts.map((a) => {
                const account = accounts.find((x) => x.id === a.id);
                return (
                  <tr key={a.id}>
                    <th scope="row" dir="auto">
                      {account ? accountName(account) : (a.name ?? a.currency)}
                    </th>
                    <td>
                      <Money value={a.balance} display="code" />
                    </td>
                    <td>
                      {a.equivalent ? (
                        <>
                          {"≈ "}
                          <Money value={a.equivalent} />
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function BudgetsPanel() {
  const t = useTranslations("app.analytics.budgets");
  const user = useUser();
  const month = todayIn(user.time_zone).slice(0, 7);
  const { data, error } = useLoad(`budgets:${month}`, () => budgetsApi.month(month));
  const { categories } = useFinance();
  const categoryName = useCategoryName();
  const locale = useLocale() as Locale;
  return (
    <div className={s.overview}>
      <h2 className={s.sectionTitle}>{t("title")}</h2>
      {error ? <Notice tone="error">{t("failed")}</Notice> : null}
      {!data ? (
        <p className={s.muted}>
          <Spinner />
        </p>
      ) : data.budgets.length === 0 ? (
        <p className={s.muted}>
          {t("empty")}{" "}
          <Link href="/app/budgets" className={s.inlineLink}>
            {t("toBudgets")}
          </Link>
        </p>
      ) : (
        <ul className={s.budgetList}>
          {data.budgets.map((b) => {
            const category = b.category
              ? categories.find((c) => c.id === b.category?.id)
              : undefined;
            return (
              <li key={`${b.category?.id ?? "all"}-${b.currency}`}>
                <span className={s.budgetName} dir="auto">
                  {category ? categoryName(category) : b.category ? t("category") : t("overall")}
                </span>
                <span className={s.budgetFigures}>
                  {`${formatMoney(b.spent, locale)} / ${formatMoney(b.limit, locale)}`}
                </span>
                <span className={s.budgetTrack} aria-hidden="true">
                  <span
                    className={s.budgetFill}
                    data-state={b.state}
                    style={{ inlineSize: `${Math.min(Number(b.percent), 100)}%` }}
                  />
                </span>
                <Tag tone={b.state === "exceeded" ? "accent" : "neutral"}>
                  {t(`states.${b.state}`, { percent: b.percent })}
                </Tag>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function TripsPanel() {
  const t = useTranslations("app.analytics.trips");
  const phase = useTranslations("app.trips.phases");
  const locale = useLocale() as Locale;
  const { data, error } = useLoad("trips-list", tripsApi.list);
  return (
    <div className={s.overview}>
      <h2 className={s.sectionTitle}>{t("title")}</h2>
      <p className={s.muted}>{t("lead")}</p>
      {error ? <Notice tone="error">{t("failed")}</Notice> : null}
      {!data ? (
        <p className={s.muted}>
          <Spinner />
        </p>
      ) : data.length === 0 ? (
        <p className={s.muted}>{t("empty")}</p>
      ) : (
        <ul className={s.budgetList}>
          {data.map((trip) => (
            <li key={trip.id}>
              <Link href={`/app/trips/${trip.id}`} className={s.budgetName} dir="auto">
                {trip.name}
              </Link>
              <span className={s.budgetFigures}>
                {trip.start_date ? formatDate(`${trip.start_date}T12:00:00Z`, locale, "UTC") : ""}
              </span>
              <Tag>{phase(trip.phase)}</Tag>
              <Link href={`/app/trips/${trip.id}/report`} className={s.inlineLink}>
                {t("report")}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
