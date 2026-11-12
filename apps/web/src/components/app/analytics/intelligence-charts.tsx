"use client";

import { useLocale, useTranslations } from "next-intl";
import { type CSSProperties, useState } from "react";
import type { Locale } from "@/i18n/routing";
import type { IntelligenceBudget, IntelligenceHeatmap, IntelligenceMonth } from "@/lib/analytics";
import { ratio } from "@/lib/analytics";
import { formatMoney, type Money as MoneyValue } from "@/lib/money";
import { Money } from "../accounts/parts";
import { useCategoryName } from "../finance/parts";
import { usePercent } from "./budgets-view";
import { useBucketLabel } from "./charts";
import s from "./intelligence.module.css";

const number = (amount: string) => {
  const n = Number(amount);
  return Number.isFinite(n) ? n : 0;
};

export const FIXED_SERIES = ["scheduled", "variable", "financed", "trip"] as const;
type SeriesKey = (typeof FIXED_SERIES)[number];

/** Months as stacked columns: the four parts of spending, which never overlap. */
export function StackedMonths({ months, label }: { months: IntelligenceMonth[]; label: string }) {
  const t = useTranslations("app.intelligence.fixed");
  const locale = useLocale() as Locale;
  const monthLabel = useBucketLabel();
  const [picked, setPicked] = useState<number | null>(null);
  const totals = months.map((m) =>
    FIXED_SERIES.reduce((sum, key) => sum + Math.max(number(m[key].amount), 0), 0),
  );
  const largest = Math.max(...totals, 1);
  const active = picked === null ? null : months[picked];
  return (
    <div className={s.stackedWrap}>
      <div className={s.legend}>
        {FIXED_SERIES.map((key) => (
          <span key={key}>
            <i className={s.swatch} data-series={key} aria-hidden="true" />
            {t(key)}
          </span>
        ))}
      </div>
      <div className={s.columns} role="group" aria-label={label}>
        {months.map((m, i) => (
          <button
            key={m.month}
            type="button"
            className={s.column}
            aria-pressed={picked === i}
            aria-label={`${monthLabel(m.month, "month", "long")}: ${formatMoney(m.spent, locale)}`}
            onClick={() => setPicked(picked === i ? null : i)}
            onPointerEnter={(e) => {
              if (e.pointerType === "mouse") setPicked(i);
            }}
          >
            <span
              className={s.stack}
              style={{ "--h": (totals[i] ?? 0) / largest } as CSSProperties}
            >
              {FIXED_SERIES.map((key) => (
                <span
                  key={key}
                  className={s.segment}
                  data-series={key}
                  style={{
                    flexGrow: Math.max(number(m[key].amount), 0),
                  }}
                />
              ))}
            </span>
            <span className={s.columnLabel}>{monthLabel(m.month, "month")}</span>
          </button>
        ))}
      </div>
      <p className={s.inspect} aria-live="polite">
        {active ? (
          <>
            <strong>{monthLabel(active.month, "month", "long")}</strong>
            {FIXED_SERIES.map((key: SeriesKey) => (
              <span key={key}>
                {t(key)} <Money value={active[key]} />
              </span>
            ))}
          </>
        ) : (
          t("inspect")
        )}
      </p>
    </div>
  );
}

export function StackedTable({
  months,
  caption,
}: {
  months: IntelligenceMonth[];
  caption: string;
}) {
  const t = useTranslations("app.intelligence.fixed");
  const monthLabel = useBucketLabel();
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <caption className={s.srOnly}>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{t("month")}</th>
            {FIXED_SERIES.map((key) => (
              <th scope="col" key={key}>
                {t(key)}
              </th>
            ))}
            <th scope="col">{t("total")}</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month}>
              <th scope="row">{monthLabel(m.month, "month", "long")}</th>
              {FIXED_SERIES.map((key) => (
                <td key={key}>
                  <Money value={m[key]} />
                </td>
              ))}
              <td>
                <Money value={m.spent} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export type BarItem = { key: string; label: string; long: string; value: MoneyValue };

/** Simple columns with one value each: the days of a period, or the days of the week. */
export function ValueColumns({
  items,
  label,
  hint,
}: {
  items: BarItem[];
  label: string;
  hint: string;
}) {
  const locale = useLocale() as Locale;
  const [picked, setPicked] = useState<string | null>(null);
  const largest = Math.max(...items.map((i) => number(i.value.amount)), 1);
  const active = items.find((i) => i.key === picked);
  const every = items.length > 16 ? Math.ceil(items.length / 8) : 1;
  return (
    <div className={s.stackedWrap}>
      <div className={s.columns} role="group" aria-label={label}>
        {items.map((item, i) => (
          <button
            key={item.key}
            type="button"
            className={s.column}
            aria-pressed={picked === item.key}
            aria-label={`${item.long}: ${formatMoney(item.value, locale)}`}
            onClick={() => setPicked(picked === item.key ? null : item.key)}
            onPointerEnter={(e) => {
              if (e.pointerType === "mouse") setPicked(item.key);
            }}
          >
            <span
              className={s.stack}
              style={{ "--h": Math.max(number(item.value.amount), 0) / largest } as CSSProperties}
            >
              <span className={s.segment} data-series="variable" style={{ flexGrow: 1 }} />
            </span>
            <span className={s.columnLabel}>{i % every === 0 ? item.label : ""}</span>
          </button>
        ))}
      </div>
      <p className={s.inspect} aria-live="polite">
        {active ? (
          <>
            <strong>{active.long}</strong>
            <Money value={active.value} />
          </>
        ) : (
          hint
        )}
      </p>
    </div>
  );
}

export function ValueTable({
  items,
  caption,
  head,
}: {
  items: BarItem[];
  caption: string;
  head: [string, string];
}) {
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <caption className={s.srOnly}>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{head[0]}</th>
            <th scope="col">{head[1]}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.key}>
              <th scope="row">{item.long}</th>
              <td>
                <Money value={item.value} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Categories by month: each cell's depth is that month against the category's own largest. */
export function CategoryHeatmap({ data }: { data: IntelligenceHeatmap }) {
  const t = useTranslations("app.intelligence.heatmap");
  const categoryName = useCategoryName();
  const monthLabel = useBucketLabel();
  const locale = useLocale() as Locale;
  return (
    <div className={s.tableWrap}>
      <table className={s.heat}>
        <caption className={s.srOnly}>{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("category")}</th>
            {data.months.map((m) => (
              <th scope="col" key={m}>
                {monthLabel(m, "month")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => {
            const largest = Math.max(...row.values.map((v) => number(v.amount)), 1);
            return (
              <tr key={row.category?.id ?? "none"}>
                <th scope="row">
                  <bdi>{row.category ? categoryName(row.category) : t("none")}</bdi>
                </th>
                {row.values.map((value, i) => (
                  <td
                    key={data.months[i]}
                    style={
                      { "--depth": Math.max(number(value.amount), 0) / largest } as CSSProperties
                    }
                    title={formatMoney(value, locale)}
                  >
                    <Money value={value} />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Each budget: how much of its limit is used, on one scale where the tick is the limit. */
export function BudgetBullets({ budgets }: { budgets: IntelligenceBudget[] }) {
  const t = useTranslations("app.intelligence.budgets");
  const categoryName = useCategoryName();
  const percent = usePercent();
  return (
    <ul className={s.bullets}>
      {budgets.map((b) => (
        <li key={`${b.category?.id ?? "all"}`}>
          <span className={s.bulletHead}>
            <bdi>{b.category ? categoryName(b.category) : t("overall")}</bdi>
            <span className={s.bulletState} data-state={b.state}>
              {t(`state.${b.state}`, { percent: percent(b.percent) })}
            </span>
          </span>
          <span className={s.bulletTrack} aria-hidden="true">
            <span
              className={s.bulletFill}
              data-state={b.state}
              style={
                {
                  "--fill": Math.min(ratio(b.spent.amount, b.limit.amount), 1.25) / 1.25,
                } as CSSProperties
              }
            />
            <span className={s.bulletLimit} />
          </span>
          <span className={s.bulletFigures}>
            <Money value={b.spent} /> {" / "} <Money value={b.limit} />
          </span>
        </li>
      ))}
    </ul>
  );
}
