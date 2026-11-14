"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { Link } from "@/i18n/navigation";
import { formatDate } from "@/lib/format";
import { type CurrencyOverview, installmentsApi, ratioPercent } from "@/lib/installments";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import { Money } from "../accounts/parts";
import appStyles from "../app.module.css";
import { Loading, Notice, Tag } from "../ui";
import { useLoad } from "../use-load";
import s from "./installments.module.css";

/** Typed text in the reader's language as a number, 0 if it isn't a valid amount. */
function numberFrom(text: string, locale: Locale): number {
  const parsed = parseAmount(text, locale, 2);
  return parsed.ok ? Number(parsed.amount) : 0;
}

/**
 * A what-if, for thinking only: the share of a monthly income that installments would take with
 * one more. It starts from recorded figures, which the person can change. Nothing is saved, nothing
 * is advice, and it never says whether anyone qualifies for credit.
 */
export function WhatIf({ overview }: { overview: CurrencyOverview | undefined }) {
  const t = useTranslations("app.installments.whatIf");
  const locale = useLocale() as Locale;
  const id = useId();
  const start = (m: { amount: string } | null | undefined) =>
    m ? editableAmount(m.amount, locale) : "";
  const [income, setIncome] = useState(start(overview?.average_income));
  const [current, setCurrent] = useState(start(overview?.monthly));
  const [extra, setExtra] = useState("");
  const currency = overview?.currency ?? "";
  const incomeValue = numberFrom(income, locale);
  const currentValue = numberFrom(current, locale);
  const extraValue = numberFrom(extra, locale);
  const now = ratioPercent(currentValue, incomeValue);
  const after = ratioPercent(currentValue + extraValue, incomeValue);
  const left = incomeValue - currentValue - extraValue;
  const pct = (value: number | null) =>
    value === null
      ? "—"
      : new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 }).format(
          value / 100,
        );

  return (
    <section className={s.whatIf} aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`} className={s.subTitle}>
        {t("title")}
      </h3>
      <p className={s.note}>{t("lead")}</p>
      <div className={s.whatIfFields}>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-income`}>
            {t("income", { currency })}
          </label>
          <input
            id={`${id}-income`}
            className={appStyles.input}
            inputMode="decimal"
            dir="ltr"
            value={income}
            onChange={(e) => setIncome(e.target.value)}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-current`}>
            {t("current", { currency })}
          </label>
          <input
            id={`${id}-current`}
            className={appStyles.input}
            inputMode="decimal"
            dir="ltr"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-extra`}>
            {t("extra", { currency })}
          </label>
          <input
            id={`${id}-extra`}
            className={appStyles.input}
            inputMode="decimal"
            dir="ltr"
            value={extra}
            onChange={(e) => setExtra(e.target.value)}
          />
        </div>
      </div>
      <dl className={s.whatIfResult} aria-live="polite">
        <div>
          <dt>{t("now")}</dt>
          <dd>{pct(now)}</dd>
        </div>
        <div>
          <dt>{t("after")}</dt>
          <dd>{pct(after)}</dd>
        </div>
        <div>
          <dt>{t("perMonthAfter")}</dt>
          <dd dir="ltr">
            {incomeValue > 0 && currency
              ? formatMoney({ amount: left.toFixed(2), currency }, locale, { signed: left < 0 })
              : "—"}
          </dd>
        </div>
      </dl>
      <p className={s.note}>{t("limits")}</p>
    </section>
  );
}

/** Committed monthly amounts, what's due when, overdue installments and the share of income. */
export function InstallmentAnalytics({ showLinks = true }: { showLinks?: boolean }) {
  const t = useTranslations("app.installments.analytics");
  const locale = useLocale() as Locale;
  const { data, error } = useLoad("installments-overview", installmentsApi.overview);

  if (error) return <Notice tone="error">{t("failed")}</Notice>;
  if (!data) return <Loading label={t("loading")} />;
  if (data.currencies.length === 0)
    return (
      <div className={s.empty}>
        <p>{t("empty")}</p>
        {showLinks ? (
          <Link href="/app/installments" className={s.link}>
            {t("toInstallments")}
          </Link>
        ) : null}
      </div>
    );

  return (
    <div className={s.analytics}>
      {data.currencies.map((c) => (
        <section key={c.currency} className={s.card} aria-label={c.currency}>
          <dl className={s.figures}>
            <div>
              <dt title={t("monthlyHelp")}>{t("monthly")}</dt>
              <dd>
                <Money value={c.monthly} />
              </dd>
            </div>
            <div>
              <dt>{t("remaining")}</dt>
              <dd>
                <Money value={c.remaining_obligations} />
              </dd>
            </div>
            <div>
              <dt>{t("count")}</dt>
              <dd>{c.remaining_count}</dd>
            </div>
            <div>
              <dt title={t("ratioHelp")}>{t("ratio")}</dt>
              <dd>
                {c.income_ratio_percent !== null
                  ? new Intl.NumberFormat(locale, {
                      style: "percent",
                      maximumFractionDigits: 1,
                    }).format(Number(c.income_ratio_percent) / 100)
                  : "—"}
              </dd>
            </div>
          </dl>
          <p className={s.note}>
            {c.average_income
              ? t("ratioBasis", {
                  income: formatMoney(c.average_income, locale),
                  months: c.income_months,
                })
              : t("noIncome", { currency: c.currency })}
          </p>
          {c.scheduled_interest.amount !== "0" && Number(c.scheduled_interest.amount) > 0 ? (
            <p className={s.note}>
              {t("interestLeft", { amount: formatMoney(c.scheduled_interest, locale) })}
            </p>
          ) : null}
          {c.overdue_count > 0 ? (
            <Notice tone="error">
              {t("overdue", { count: c.overdue_count, amount: formatMoney(c.overdue, locale) })}
            </Notice>
          ) : null}
          <table className={s.windows}>
            <caption className={s.caption}>{t("dueTitle")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("window")}</th>
                <th scope="col">{t("amount")}</th>
              </tr>
            </thead>
            <tbody>
              {c.due_within.map((w) => (
                <tr key={w.months}>
                  <th scope="row">{t("within", { count: w.months })}</th>
                  <td>
                    <Money value={w.amount} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {c.last_payoff ? (
            <p className={s.note}>
              {t("payoff", { date: formatDate(`${c.last_payoff}T12:00:00Z`, locale, "UTC") })}
            </p>
          ) : null}
          <WhatIf overview={c} />
        </section>
      ))}

      {data.upcoming.length > 0 ? (
        <section className={s.card} aria-labelledby="installments-upcoming">
          <h3 id="installments-upcoming" className={s.subTitle}>
            {t("upcoming")}
          </h3>
          <ul className={s.upcoming}>
            {data.upcoming.slice(0, 12).map((u) => (
              <li key={`${u.plan_id}-${u.sequence}`}>
                <span className={s.upDate}>
                  {formatDate(`${u.due_date}T12:00:00Z`, locale, "UTC")}
                </span>
                <Link href={`/app/installments/${u.plan_id}`} className={s.upName} dir="auto">
                  {u.plan_name}
                </Link>
                <span className={s.upAmount}>
                  <Money value={u.amount} />
                </span>
                {u.state === "overdue" ? <Tag tone="accent">{t("overdueTag")}</Tag> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <p className={s.note}>{t("estimates")}</p>
    </div>
  );
}
