"use client";

import { ChartColumn, Download, Printer, SlidersHorizontal } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";
import type { CategoryKey } from "./data";
import { useFormatters } from "./format";
import s from "./landing.module.css";
import { useReveal } from "./motion";

type Scope = "all" | "food";
type Row = {
  key: "rent" | "dinner" | "migros" | "pazar" | "metro" | "cafe";
  cat: CategoryKey;
  amount: number;
  trip?: boolean;
  food: boolean;
};

// Illustrative October. "spent" counts your share; "cash" is what left your accounts.
const SUMMARY: Record<
  Scope,
  Record<"with" | "without", { spent: number; cash: number; count: number }>
> = {
  all: {
    with: { spent: 1846.4, cash: 2046.4, count: 46 },
    without: { spent: 1746.4, cash: 1746.4, count: 45 },
  },
  food: {
    with: { spent: 600.9, cash: 800.9, count: 19 },
    without: { spent: 500.9, cash: 500.9, count: 18 },
  },
};
const ROWS: Row[] = [
  { key: "rent", cat: "rent", amount: 950, food: false },
  { key: "dinner", cat: "eating", amount: 100, trip: true, food: true },
  { key: "migros", cat: "groceries", amount: 42.3, food: true },
  { key: "pazar", cat: "groceries", amount: 38.9, food: true },
  { key: "metro", cat: "transport", amount: 20, food: false },
  { key: "cafe", cat: "eating", amount: 12.4, food: true },
];

const FEATURES = [
  { key: "summaries", icon: ChartColumn },
  { key: "filters", icon: SlidersHorizontal },
  { key: "export", icon: Download },
  { key: "print", icon: Printer },
] as const;

export function Reports() {
  const t = useTranslations("landing.reports");
  const cat = useTranslations("landing.cat");
  const f = useFormatters();
  const ref = useRef<HTMLElement>(null);
  const [trips, setTrips] = useState(true);
  const [scope, setScope] = useState<Scope>("all");
  useReveal(ref, 0.2);

  const sum = SUMMARY[scope][trips ? "with" : "without"];
  const rows = ROWS.filter((r) => (scope === "all" || r.food) && (trips || !r.trip)).slice(0, 4);

  return (
    <section
      data-surface="page"
      ref={ref}
      id="reports"
      className={s.reports}
      aria-labelledby="reports-title"
    >
      <div className={s.wrap}>
        <header className={s.sectionHead}>
          <h2 id="reports-title">{t("title")}</h2>
          <p className={s.lede}>{t("lede")}</p>
        </header>

        <div className={s.reportsGrid}>
          <figure className={s.sheets} style={{ margin: 0 }} aria-labelledby="report-caption">
            <div className={s.sheetBack} aria-hidden="true" />
            <div className={s.sheet}>
              <div className={s.sheetHead}>
                <div>
                  <h3>{t("sheet.title")}</h3>
                  <small>{f.date(9, 1, { month: "long", year: "numeric" })}</small>
                </div>
                <span className={s.example}>{t("sheet.preview")}</span>
              </div>
              <div className={s.filters}>
                <button
                  type="button"
                  className={s.toggle}
                  aria-pressed={trips}
                  onClick={() => setTrips((v) => !v)}
                >
                  <span className={s.switch} aria-hidden="true" />
                  {t("sheet.trips")}
                </button>
                <button
                  type="button"
                  className={s.toggle}
                  aria-pressed={scope === "food"}
                  onClick={() => setScope((v) => (v === "all" ? "food" : "all"))}
                >
                  <span className={s.switch} aria-hidden="true" />
                  {t("sheet.foodOnly")}
                </button>
              </div>
              <div className={s.sheetStats} aria-live="polite">
                <div>
                  <span>{t("sheet.spent")}</span>
                  <strong className={s.num}>{f.money(sum.spent)}</strong>
                </div>
                <div>
                  <span>{t("sheet.cash")}</span>
                  <strong className={s.num}>{f.money(sum.cash)}</strong>
                </div>
                <div>
                  <span>{t("sheet.count")}</span>
                  <strong className={s.num}>{sum.count}</strong>
                </div>
              </div>
              <table className={s.sheetTable}>
                <thead>
                  <tr>
                    <th scope="col">{t("sheet.item")}</th>
                    <th scope="col">{t("sheet.category")}</th>
                    <th scope="col">{t("sheet.amount")}</th>
                  </tr>
                </thead>
                <tbody key={`${scope}-${trips}`}>
                  {rows.map((r, i) => (
                    <tr key={r.key} style={{ animationDelay: `${i * 60}ms` }}>
                      <td>
                        {t(`rows.${r.key}`)}
                        {r.trip ? (
                          <small className={s.todayMeta}> {t("sheet.tripShare")}</small>
                        ) : null}
                      </td>
                      <td>{cat(r.cat)}</td>
                      <td className={s.num}>{f.money(r.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <figcaption
              id="report-caption"
              className={s.disclaimer}
              style={{ marginBlockStart: 16 }}
            >
              {t("caption")}
            </figcaption>
          </figure>

          <ul className={s.features}>
            {FEATURES.map(({ key, icon: Icon }) => (
              <li key={key} className={s.feature}>
                <span className={s.featureIcon}>
                  <Icon aria-hidden="true" />
                </span>
                <span className={s.featureTitle}>{t(`features.${key}.title`)}</span>
                <span className={s.featureBody}>{t(`features.${key}.body`)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
