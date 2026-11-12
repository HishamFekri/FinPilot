"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useCallback, useRef, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { type CategoryShare, type Grain, ratio, type SeriesPoint } from "@/lib/analytics";
import { localeTag } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { cn } from "@/lib/utils";
import { Money } from "../accounts/parts";
import { CategoryIcon, useCategoryName } from "../finance/parts";
import { usePercent } from "./budgets-view";
import s from "./analytics.module.css";

/**
 * Charts for Insights and Reports (dataviz skill): one axis, thin marks, a legend whenever there
 * are two series, a hover tooltip, and a table with the same figures for every chart. Amounts are
 * the API's decimal strings; they become numbers only to work out where to draw.
 */

const draw = (amount: string) => {
  const n = Number(amount);
  return Number.isFinite(n) ? n : 0;
};

/** Width of the element, kept current with a ResizeObserver. */
function useWidth(): [(node: HTMLDivElement | null) => void, number] {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((node: HTMLDivElement | null) => {
    observer.current?.disconnect();
    if (!node) return;
    setWidth(node.clientWidth);
    observer.current = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.floor(entry.contentRect.width));
    });
    observer.current.observe(node);
  }, []);
  return [ref, width];
}

/** "Nice" round ticks from zero to at least the largest value. */
export function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const rough = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * magnitude >= rough) ?? 10) * magnitude;
  const ticks = [];
  for (let v = 0; v < max + step * 0.999; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

function useAxisNumber() {
  const locale = useLocale() as Locale;
  const format = new Intl.NumberFormat(localeTag(locale), {
    notation: "compact",
    maximumFractionDigits: 1,
  });
  return (value: number) => format.format(value);
}

export function useBucketLabel() {
  const locale = useLocale() as Locale;
  const tag = localeTag(locale);
  return (bucket: string, grain: Grain, style: "short" | "long" = "short") => {
    const date = new Date(`${bucket}T12:00:00Z`);
    if (grain === "month") {
      return new Intl.DateTimeFormat(tag, {
        month: style === "long" ? "long" : "short",
        year: style === "long" ? "numeric" : "2-digit",
        timeZone: "UTC",
      }).format(date);
    }
    return new Intl.DateTimeFormat(tag, {
      day: "numeric",
      month: "short",
      ...(style === "long" ? { year: "numeric" } : {}),
      timeZone: "UTC",
    }).format(date);
  };
}

/** A chart with a "Chart / Table" switch. The table is the same data, readable by everyone. */
export function ChartCard({
  title,
  id,
  note,
  chart,
  table,
  extra,
}: {
  title: string;
  id: string;
  note?: ReactNode;
  chart: ReactNode;
  table: ReactNode;
  extra?: ReactNode;
}) {
  const t = useTranslations("app.insights");
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <section className={s.card} aria-labelledby={`${id}-title`}>
      <div className={s.cardHead}>
        <h2 id={`${id}-title`} className={s.cardTitle}>
          {title}
        </h2>
        <span className={s.headActions}>
          {extra}
          <span className={cn(s.viewToggle, s.noPrint)} role="group" aria-label={t("view")}>
            <button type="button" aria-pressed={view === "chart"} onClick={() => setView("chart")}>
              {t("chartView")}
            </button>
            <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>
              {t("tableView")}
            </button>
          </span>
        </span>
      </div>
      <div className={s.cardBody}>
        {note}
        {view === "chart" ? chart : table}
      </div>
    </section>
  );
}

// --- Spending by category: ranked horizontal bars, one hue, every value written beside its bar.

export function CategoryBars({ items, limit }: { items: CategoryShare[]; limit?: number }) {
  const t = useTranslations("app.insights");
  const categoryName = useCategoryName();
  const percent = usePercent();
  const shown = limit ? items.slice(0, limit) : items;
  const largest = items[0]?.amount.amount ?? "0";
  if (items.length === 0) return <p className={s.note}>{t("noSpending")}</p>;
  return (
    <ol className={s.catList}>
      {shown.map((item) => (
        <li key={item.category?.id ?? "none"} className={s.catItem}>
          {item.category ? <CategoryIcon category={item.category} size="sm" /> : <span />}
          <span className={s.catName} dir="auto">
            {item.category ? categoryName(item.category) : t("uncategorized")}
          </span>
          <Money value={item.amount} className={s.catAmount} />
          <span className={s.catShare}>
            <bdi dir="ltr">{t("share", { percent: percent(item.percent) })}</bdi>
          </span>
          <span className={s.catTrack} aria-hidden="true">
            <span
              className={s.catFill}
              style={{
                inlineSize: `${Math.max(2, ratio(item.amount.amount, largest) * 100)}%`,
              }}
            />
          </span>
        </li>
      ))}
    </ol>
  );
}

export function CategoryTable({ items, caption }: { items: CategoryShare[]; caption: string }) {
  const t = useTranslations("app.insights");
  const categoryName = useCategoryName();
  const percent = usePercent();
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{t("category")}</th>
            <th scope="col" className={s.num}>
              {t("amount")}
            </th>
            <th scope="col" className={s.num}>
              {t("shareHead")}
            </th>
            <th scope="col" className={s.num}>
              {t("previous")}
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.category?.id ?? "none"}>
              <th scope="row" dir="auto">
                {item.category ? categoryName(item.category) : t("uncategorized")}
              </th>
              <td className={s.num}>
                <Money value={item.amount} />
              </td>
              <td className={s.num}>
                <bdi dir="ltr">{t("share", { percent: percent(item.percent) })}</bdi>
              </td>
              <td className={s.num}>
                <Money value={item.previous} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Income vs spending by period: paired vertical bars on one axis.

type Hover = { index: number; x: number } | null;

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x - 80, 0), Math.max(width - 170, 0));
  return (
    <div className={s.tooltip} style={{ left }} role="presentation">
      {children}
    </div>
  );
}

export function SeriesTable({
  points,
  grain,
  caption,
  spendingOnly,
}: {
  points: SeriesPoint[];
  grain: Grain;
  caption: string;
  spendingOnly?: boolean;
}) {
  const t = useTranslations("app.insights");
  const label = useBucketLabel();
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{t(`grain.${grain}`)}</th>
            {spendingOnly ? null : (
              <th scope="col" className={s.num}>
                {t("income")}
              </th>
            )}
            <th scope="col" className={s.num}>
              {t("spending")}
            </th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.bucket}>
              <th scope="row">
                <bdi>{label(p.bucket, grain, "long")}</bdi>
              </th>
              {spendingOnly ? null : (
                <td className={s.num}>
                  <Money value={p.income} />
                </td>
              )}
              <td className={s.num}>
                <Money value={p.expenses} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PAD = { top: 10, right: 8, bottom: 26, left: 44 };

export function IncomeSpendingBars({
  points,
  grain,
  label: ariaLabel,
}: {
  points: SeriesPoint[];
  grain: Grain;
  label: string;
}) {
  const t = useTranslations("app.insights");
  const locale = useLocale() as Locale;
  const axis = useAxisNumber();
  const bucket = useBucketLabel();
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<Hover>(null);
  const height = 200;
  const max = Math.max(...points.flatMap((p) => [draw(p.income.amount), draw(p.expenses.amount)]));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] ?? 1;
  const innerW = Math.max(width - PAD.left - PAD.right, 10);
  const innerH = height - PAD.top - PAD.bottom;
  const band = innerW / Math.max(points.length, 1);
  const barW = Math.max(2, Math.min(18, (band - 6) / 2));
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH;
  const every = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(innerW / 44))));
  const active = hover ? points[hover.index] : null;

  return (
    <div>
      <ul className={s.legend}>
        <li>
          <span className={cn(s.swatch, s.swatchIncome)} aria-hidden="true" />
          {t("income")}
        </li>
        <li>
          <span className={cn(s.swatch, s.swatchSpend)} aria-hidden="true" />
          {t("spending")}
        </li>
      </ul>
      <div ref={ref} className={s.chartWrap} onMouseLeave={() => setHover(null)}>
        {width > 0 ? (
          <svg
            className={s.chart}
            width={width}
            height={height}
            role="img"
            aria-label={ariaLabel}
            style={{ direction: "ltr" }}
          >
            {ticks.map((v) => (
              <g key={v}>
                <line
                  className={s.gridLine}
                  x1={PAD.left}
                  x2={width - PAD.right}
                  y1={y(v)}
                  y2={y(v)}
                />
                <text className={s.axisText} x={PAD.left - 6} y={y(v) + 4} textAnchor="end">
                  {axis(v)}
                </text>
              </g>
            ))}
            {points.map((p, i) => {
              const cx = PAD.left + band * i + band / 2;
              const inc = draw(p.income.amount);
              const exp = draw(p.expenses.amount);
              const on = hover?.index === i;
              return (
                <g key={p.bucket}>
                  {inc > 0 ? (
                    <path
                      className={cn(s.barIncome, hover && !on && s.barActive)}
                      d={barPath(cx - barW - 1, y(inc), barW, y(0) - y(inc))}
                    />
                  ) : null}
                  {exp > 0 ? (
                    <path
                      className={cn(s.barSpend, hover && !on && s.barActive)}
                      d={barPath(cx + 1, y(exp), barW, y(0) - y(exp))}
                    />
                  ) : null}
                  {i % every === 0 ? (
                    <text className={s.axisText} x={cx} y={height - 8} textAnchor="middle">
                      {bucket(p.bucket, grain)}
                    </text>
                  ) : null}
                  <rect
                    className={s.hit}
                    x={PAD.left + band * i}
                    y={PAD.top}
                    width={band}
                    height={innerH}
                    onMouseEnter={() => setHover({ index: i, x: cx })}
                    onMouseMove={() => setHover({ index: i, x: cx })}
                  />
                </g>
              );
            })}
            <line
              className={s.gridLine}
              x1={PAD.left}
              x2={width - PAD.right}
              y1={y(0)}
              y2={y(0)}
              style={{ stroke: "var(--hair)" }}
            />
          </svg>
        ) : (
          <div style={{ blockSize: height }} />
        )}
        {active && hover ? (
          <Tooltip x={hover.x} width={width}>
            <strong>{bucket(active.bucket, grain, "long")}</strong>
            <span className={s.tipRow}>
              <span>
                <span className={cn(s.swatch, s.swatchIncome)} aria-hidden="true" />
                {t("income")}
              </span>
              <bdi dir="ltr">{formatMoney(active.income, locale)}</bdi>
            </span>
            <span className={s.tipRow}>
              <span>
                <span className={cn(s.swatch, s.swatchSpend)} aria-hidden="true" />
                {t("spending")}
              </span>
              <bdi dir="ltr">{formatMoney(active.expenses, locale)}</bdi>
            </span>
          </Tooltip>
        ) : null}
      </div>
    </div>
  );
}

/** A bar with 4px rounded data-end, square at the baseline. */
function barPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

// --- Spending over days (or weeks): a 2px line with a soft area, crosshair and tooltip.

export function SpendingLine({
  points,
  grain,
  label: ariaLabel,
}: {
  points: SeriesPoint[];
  grain: Grain;
  label: string;
}) {
  const t = useTranslations("app.insights");
  const locale = useLocale() as Locale;
  const axis = useAxisNumber();
  const bucket = useBucketLabel();
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<Hover>(null);
  const height = 180;
  const max = Math.max(...points.map((p) => draw(p.expenses.amount)));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] ?? 1;
  const innerW = Math.max(width - PAD.left - PAD.right, 10);
  const innerH = height - PAD.top - PAD.bottom;
  const step = points.length > 1 ? innerW / (points.length - 1) : 0;
  const x = (i: number) => (points.length > 1 ? PAD.left + step * i : PAD.left + innerW / 2);
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH;
  const coords = points.map((p, i) => [x(i), y(draw(p.expenses.amount))] as const);
  const line = coords.map(([cx, cy], i) => `${i ? "L" : "M"}${cx},${cy}`).join("");
  const area = coords.length
    ? `${line}L${coords[coords.length - 1]?.[0]},${y(0)}L${coords[0]?.[0]},${y(0)}Z`
    : "";
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(innerW / 64))));
  const active = hover ? points[hover.index] : null;

  const pick = (clientX: number, rect: DOMRect) => {
    const local = clientX - rect.left - PAD.left;
    const index = Math.min(points.length - 1, Math.max(0, Math.round(local / (step || 1))));
    setHover({ index, x: x(index) });
  };

  return (
    <div ref={ref} className={s.chartWrap} onMouseLeave={() => setHover(null)}>
      {width > 0 ? (
        <svg
          className={s.chart}
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel}
          style={{ direction: "ltr" }}
        >
          {ticks.map((v) => (
            <g key={v}>
              <line
                className={s.gridLine}
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y(v)}
                y2={y(v)}
              />
              <text className={s.axisText} x={PAD.left - 6} y={y(v) + 4} textAnchor="end">
                {axis(v)}
              </text>
            </g>
          ))}
          <path className={s.areaSpend} d={area} />
          <path className={s.lineSpend} d={line} />
          {points.map((p, i) =>
            i % labelEvery === 0 ? (
              <text
                key={p.bucket}
                className={s.axisText}
                x={x(i)}
                y={height - 8}
                textAnchor={i === 0 ? "start" : "middle"}
              >
                {bucket(p.bucket, grain)}
              </text>
            ) : null,
          )}
          {active && hover ? (
            <>
              <line
                className={s.crosshair}
                x1={hover.x}
                x2={hover.x}
                y1={PAD.top}
                y2={PAD.top + innerH}
              />
              <circle
                className={s.dot}
                cx={hover.x}
                cy={y(draw(active.expenses.amount))}
                r={4.5}
                style={{ fill: "var(--series-spend)" }}
              />
            </>
          ) : null}
          <rect
            className={s.hit}
            x={PAD.left - 6}
            y={PAD.top}
            width={innerW + 12}
            height={innerH}
            onMouseMove={(e) =>
              pick(
                e.clientX,
                (e.currentTarget.ownerSVGElement ?? e.currentTarget).getBoundingClientRect(),
              )
            }
          />
        </svg>
      ) : (
        <div style={{ blockSize: height }} />
      )}
      {active && hover ? (
        <Tooltip x={hover.x} width={width}>
          <strong>{bucket(active.bucket, grain, "long")}</strong>
          <span className={s.tipRow}>
            <span>
              <span className={cn(s.swatch, s.swatchSpend)} aria-hidden="true" />
              {t("spending")}
            </span>
            <bdi dir="ltr">{formatMoney(active.expenses, locale)}</bdi>
          </span>
        </Tooltip>
      ) : null}
    </div>
  );
}
