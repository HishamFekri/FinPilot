"use client";

import { ChevronRight, CircleAlert, Target } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import type { CSSProperties } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { budgetsApi } from "@/lib/analytics";
import { percentOf } from "@/lib/decimal";
import { formatDate, localeTag } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { BudgetIcon, BudgetName } from "../analytics/budgets-view";
import appStyles from "../app.module.css";
import fs from "../finance/finance.module.css";
import { useGoals } from "../goals/goals-view";
import { useSubscriptions } from "../recurring/subscriptions-view";
import { useLoad } from "../use-load";
import h from "./home.module.css";
import { Skeleton } from "./remote";

const SHOWN = 3;

/** A thin bar. Its width is a ratio for drawing, never a displayed or stored amount. */
function Bar({ percent, tone }: { percent: number; tone: string }) {
  const fill = Math.min(100, Math.max(percent, percent > 0 ? 1.5 : 0));
  return (
    <span className={h.bar} aria-hidden="true">
      <span
        className={h.barFill}
        data-tone={tone}
        style={{ "--fill": fill / 100 } as CSSProperties}
      />
    </span>
  );
}

function usePercent() {
  const locale = useLocale() as Locale;
  const format = new Intl.NumberFormat(localeTag(locale), {
    style: "percent",
    maximumFractionDigits: 0,
  });
  return (value: number) => format.format(value / 100);
}

/** This month's budgets, most used first. */
export function HomeBudgets({ month }: { month: string }) {
  const t = useTranslations("app.home.budgets");
  const locale = useLocale() as Locale;
  const percent = usePercent();
  const { data, error } = useLoad(`home-budgets:${month}`, () => budgetsApi.month(month));
  const shown = data?.budgets.slice(0, SHOWN) ?? [];
  const extra = (data?.budgets.length ?? 0) - shown.length;

  return (
    <section className={h.section} aria-labelledby="budgets-title">
      <div className={h.sectionHead}>
        <div>
          <h2 id="budgets-title" className={h.sectionTitle}>
            {t("title")}
          </h2>
          <p className={h.sectionLead}>{t("lead")}</p>
        </div>
      </div>
      {error ? (
        <p className={h.muted}>{t("failed")}</p>
      ) : !data ? (
        <Skeleton lines={2} />
      ) : shown.length === 0 ? (
        <p className={h.muted}>{t("empty")}</p>
      ) : (
        <ul className={h.progressList}>
          {shown.map((budget) => {
            const used = Number(budget.percent);
            return (
              <li key={`${budget.category?.id ?? "all"}:${budget.currency}`} className={h.progress}>
                <span className={h.progressHead}>
                  <BudgetIcon category={budget.category} variant="bare" />
                  <span className={h.progressName}>
                    <bdi>
                      <BudgetName budget={budget} />
                    </bdi>
                  </span>
                  <span className={h.progressShare} data-state={budget.state}>
                    {budget.state !== "normal" ? <CircleAlert aria-hidden="true" /> : null}
                    {percent(used)}
                  </span>
                </span>
                <Bar percent={used} tone={budget.state} />
                <span className={h.progressFigures}>
                  <bdi dir="ltr">{formatMoney(budget.spent, locale)}</bdi>
                  {" / "}
                  <bdi dir="ltr">{formatMoney(budget.limit, locale)}</bdi>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <Link href="/app/budgets" className={h.quietLink}>
        {shown.length === 0
          ? t("create")
          : extra > 0
            ? t("moreLink", { count: extra })
            : t("seeAll")}
        <ChevronRight aria-hidden="true" className={fs.flipRtl} />
      </Link>
    </section>
  );
}

/** Active savings goals, and what subscriptions cost a month. */
export function HomeGoals() {
  const t = useTranslations("app.home.goals");
  const plans = useTranslations("app.home.plans");
  const locale = useLocale() as Locale;
  const percent = usePercent();
  const goals = useGoals();
  const subs = useSubscriptions();
  const active = (goals.list ?? []).filter((g) => g.status === "active");
  const shown = active.slice(0, SHOWN);
  const costs = (subs.value?.costs ?? []).filter((c) => Number(c.monthly.amount) > 0);

  return (
    <section className={h.section} aria-labelledby="goals-title">
      <div className={h.sectionHead}>
        <div>
          <h2 id="goals-title" className={h.sectionTitle}>
            {t("title")}
          </h2>
          <p className={h.sectionLead}>{plans("goalsNote")}</p>
        </div>
      </div>
      {!goals.list ? (
        <Skeleton lines={2} />
      ) : shown.length === 0 ? (
        <p className={h.muted}>{t("empty")}</p>
      ) : (
        <ul className={h.progressList}>
          {shown.map((g) => {
            const used = percentOf(g.progress.amount, g.target.amount) ?? Number(g.percent);
            return (
              <li key={g.id} className={h.progress}>
                <span className={h.progressHead}>
                  <Target className={h.goalIcon} aria-hidden="true" />
                  <span className={h.progressName}>
                    <bdi>{g.name}</bdi>
                  </span>
                  <span className={h.progressShare} data-state="goal">
                    {percent(used)}
                  </span>
                </span>
                <Bar percent={used} tone="goal" />
                <span className={h.progressFigures}>
                  <bdi dir="ltr">{formatMoney(g.progress, locale)}</bdi>
                  {" / "}
                  <bdi dir="ltr">{formatMoney(g.target, locale)}</bdi>
                  {g.target_date ? (
                    <span className={h.muted}>
                      {" · "}
                      {t("by", { date: formatDate(`${g.target_date}T12:00:00Z`, locale, "UTC") })}
                    </span>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <Link href="/app/goals" className={h.quietLink}>
        {shown.length === 0 ? t("create") : t("all")}
        <ChevronRight aria-hidden="true" className={fs.flipRtl} />
      </Link>
      {costs.length > 0 ? (
        <Link href="/app/subscriptions" className={appStyles.small + " " + h.quietNote}>
          {plans("subscriptionsCost", {
            amounts: costs
              .map((c) => `${c.approximate ? "≈ " : ""}${formatMoney(c.monthly, locale)}`)
              .join(" · "),
          })}
        </Link>
      ) : null}
    </section>
  );
}
