"use client";

import { ChevronRight } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type CSSProperties, useId, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { buildSlices, type Slice } from "@/lib/category-chart";
import type { MonthSummary } from "@/lib/finance";
import { localeTag } from "@/lib/format";
import { formatMoney, type Money as MoneyValue } from "@/lib/money";
import { Money } from "../accounts/parts";
import { useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { CategoryIcon, useCategoryName } from "../finance/parts";
import h from "./home.module.css";
import { type Remote, Retry, Skeleton } from "./remote";

const RADIUS = 78;
const GAP = 0.9; // of 100, the sliver of background between slices

/** The ring. Decorative for assistive technology: the legend and the table carry the same data. */
function Donut({
  slices,
  active,
  onHover,
  onPick,
}: {
  slices: Slice[];
  active: string | null;
  onHover: (id: string | null) => void;
  onPick: (id: string) => void;
}) {
  const mask = useId().replaceAll(":", "");
  const gap = slices.length > 1 ? GAP : 0;
  const startOf = (index: number) =>
    slices.slice(0, index).reduce((sum, slice) => sum + slice.share * 100, 0);
  return (
    <svg className={h.donut} viewBox="0 0 200 200" aria-hidden="true">
      <mask id={mask}>
        <circle
          className={h.donutReveal}
          cx="100"
          cy="100"
          r={RADIUS}
          pathLength={100}
          transform="rotate(-90 100 100)"
        />
      </mask>
      <g mask={`url(#${mask})`}>
        {slices.map((slice, index) => {
          const length = Math.max(slice.share * 100 - gap, 0.5);
          const offset = -(startOf(index) + gap / 2);
          return (
            <circle
              key={slice.id}
              className={h.slice}
              data-color={slice.color}
              data-active={active === slice.id || undefined}
              data-dim={(active !== null && active !== slice.id) || undefined}
              cx="100"
              cy="100"
              r={RADIUS}
              pathLength={100}
              strokeDasharray={`${length} ${100 - length}`}
              strokeDashoffset={offset}
              transform="rotate(-90 100 100)"
              onPointerEnter={(e) => {
                if (e.pointerType === "mouse") onHover(slice.id);
              }}
              onPointerLeave={(e) => {
                if (e.pointerType === "mouse") onHover(null);
              }}
              onClick={() => onPick(slice.id)}
            />
          );
        })}
      </g>
    </svg>
  );
}

/**
 * Where this month's money went, in one currency at a time: amounts in different currencies are
 * never added together here. Figures are the same ones the month's totals use, so trip shares are
 * counted the same way.
 */
export function HomeCategories({
  state,
  onRetry,
}: {
  state: Remote<MonthSummary>;
  onRetry: () => void;
}) {
  const t = useTranslations("app.home.categories");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { accounts, defaultAccountId } = useFinance();
  const categoryName = useCategoryName();
  const [pickedCurrency, setPickedCurrency] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);

  const currencies = state.kind === "ready" ? state.data.currencies : [];
  const codes = currencies.filter((c) => Number(c.expenses.amount) > 0).map((c) => c.currency);
  const home =
    accounts.find((a) => a.id === defaultAccountId)?.currency ?? user.preferred_currency ?? "USD";
  const shown =
    (pickedCurrency && codes.includes(pickedCurrency) ? pickedCurrency : null) ??
    (codes.includes(home) ? home : (codes[0] ?? home));
  const figures = currencies.find((c) => c.currency === shown);

  const spending = [
    ...(figures?.categories ?? []).map((c) => ({
      id: c.category.id,
      name: categoryName(c.category),
      color: c.category.color ?? "slate",
      amount: c.amount.amount,
    })),
    ...(figures?.uncategorized
      ? [
          {
            id: "uncategorized",
            name: t("uncategorized"),
            color: "slate",
            amount: figures.uncategorized.amount,
          },
        ]
      : []),
  ];
  const slices = buildSlices(spending, t("other"));
  const percent = new Intl.NumberFormat(localeTag(locale), { style: "percent" });
  const share = (value: number) => (value === 0 ? t("tiny") : percent.format(value / 100));
  const activeId = hovered ?? pinned;
  const active = slices.find((slice) => slice.id === activeId) ?? null;
  const money = (amount: string): MoneyValue => ({ amount, currency: shown });
  const center = active ? money(active.amount) : (figures?.expenses ?? money("0"));
  const pick = (id: string) => setPinned((current) => (current === id ? null : id));
  const total = figures?.expenses;

  return (
    <section className={h.section} aria-labelledby="categories-title">
      <div className={h.sectionHead}>
        <h2 id="categories-title" className={h.sectionTitle}>
          {t("title")}
        </h2>
        {codes.length > 1 ? (
          <div className={h.currencyTabs} role="group" aria-label={t("currency")}>
            {codes.map((code) => (
              <button
                key={code}
                type="button"
                aria-pressed={code === shown}
                onClick={() => {
                  setPickedCurrency(code);
                  setPinned(null);
                }}
              >
                <bdi dir="ltr">{code}</bdi>
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {state.kind === "loading" ? (
        <Skeleton lines={4} />
      ) : state.kind === "failed" ? (
        <Retry onRetry={onRetry} />
      ) : slices.length === 0 ? (
        <div className={h.chartEmpty}>
          <span className={h.emptyRing} aria-hidden="true" />
          <p className={h.muted}>{t("empty")}</p>
        </div>
      ) : (
        <>
          <div className={h.chart}>
            <div className={h.donutWrap}>
              <Donut
                key={shown}
                slices={slices}
                active={activeId}
                onHover={setHovered}
                onPick={pick}
              />
              <div
                className={h.donutCenter}
                style={{ "--len": formatMoney(center, locale).length } as CSSProperties}
              >
                {active ? (
                  <span className={h.donutName}>
                    <bdi>{active.name}</bdi>
                  </span>
                ) : null}
                <Money value={center} className={h.donutTotal} />
                {active ? <span className={h.donutShare}>{share(active.percent)}</span> : null}
              </div>
            </div>
            <ul className={h.legend}>
              {slices.map((slice) => (
                <li key={slice.id}>
                  <button
                    type="button"
                    className={h.legendRow}
                    aria-pressed={pinned === slice.id}
                    data-active={activeId === slice.id || undefined}
                    onClick={() => pick(slice.id)}
                    onPointerEnter={(e) => {
                      if (e.pointerType === "mouse") setHovered(slice.id);
                    }}
                    onPointerLeave={(e) => {
                      if (e.pointerType === "mouse") setHovered(null);
                    }}
                    onFocus={() => setHovered(slice.id)}
                    onBlur={() => setHovered(null)}
                  >
                    <span className={h.legendDot} data-color={slice.color} aria-hidden="true" />
                    <span className={h.legendName}>
                      <bdi>{slice.name}</bdi>
                    </span>
                    <span className={h.srOnly}>
                      <Money value={money(slice.amount)} />
                    </span>
                    <span className={h.legendShare}>{share(slice.percent)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <details className={h.allCategories}>
            <summary>{t("all", { count: spending.length })}</summary>
            <table className={h.allTable}>
              <caption className={h.srOnly}>{t("title")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("category")}</th>
                  <th scope="col">{t("amount")}</th>
                </tr>
              </thead>
              <tbody>
                {(figures?.categories ?? []).map((c) => (
                  <tr key={c.category.id}>
                    <th scope="row">
                      <CategoryIcon category={c.category} variant="bare" size="sm" />
                      <span dir="auto">{categoryName(c.category)}</span>
                    </th>
                    <td>
                      <Money value={c.amount} />
                    </td>
                  </tr>
                ))}
                {figures?.uncategorized ? (
                  <tr>
                    <th scope="row">
                      <span>{t("uncategorized")}</span>
                    </th>
                    <td>
                      <Money value={figures.uncategorized} />
                    </td>
                  </tr>
                ) : null}
              </tbody>
              {total ? (
                <tfoot>
                  <tr>
                    <th scope="row">{t("total")}</th>
                    <td>
                      <Money value={total} />
                    </td>
                  </tr>
                </tfoot>
              ) : null}
            </table>
            <Link href="/app/analytics?tab=spending" className={h.quietLink}>
              {t("analytics")}
              <ChevronRight aria-hidden="true" className={fs.flipRtl} />
            </Link>
          </details>
        </>
      )}
    </section>
  );
}
