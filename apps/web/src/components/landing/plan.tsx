"use client";

import { Check } from "lucide-react";
import { useTranslations } from "next-intl";
import { type CSSProperties, useRef } from "react";
import { cn } from "@/lib/utils";
import { useFormatters } from "./format";
import s from "./landing.module.css";
import { useReveal } from "./motion";

// Example monthly plan. Percentages follow from the amounts.
const BUDGET = [
  { key: "food", spent: 292.5, limit: 450 },
  { key: "transport", spent: 42, limit: 120 },
  { key: "shopping", spent: 160, limit: 200 },
] as const;
const SPENT = BUDGET.reduce((a, b) => a + b.spent, 0);
const LIMIT = BUDGET.reduce((a, b) => a + b.limit, 0);

/** One idea, one card: a plan per category, and how much of it is used. */
export function Plan() {
  const t = useTranslations("landing.plan");
  const cat = useTranslations("landing.cat");
  const f = useFormatters();
  const ref = useRef<HTMLElement>(null);
  useReveal(ref, 0.3);

  return (
    <section
      data-surface="page"
      ref={ref}
      id="plan"
      className={s.plan}
      aria-labelledby="plan-title"
    >
      <div className={cn(s.wrap, s.planGrid)}>
        <div>
          <header className={s.sectionHead}>
            <h2 id="plan-title">{t("title")}</h2>
            <p className={s.lede}>{t("lede")}</p>
          </header>
          <ul className={s.benefits}>
            {(["one", "two", "three"] as const).map((k) => (
              <li key={k}>
                <span className={s.benefitIcon} aria-hidden="true">
                  <Check />
                </span>
                {t(`benefits.${k}`)}
              </li>
            ))}
          </ul>
        </div>

        <figure className={s.budgetCard} aria-labelledby="budget-title">
          <div className={s.budgetHead}>
            <h3 id="budget-title">{t("preview.title", { month: f.monthName(9) })}</h3>
            <span className={s.example}>{t("example")}</span>
          </div>
          <ul className={s.budgetList}>
            {BUDGET.map((b, i) => {
              const pct = Math.round((b.spent / b.limit) * 100);
              return (
                <li key={b.key} className={cn(s.budgetRow, pct >= 75 && s.budgetHigh)}>
                  <span className={s.budgetName}>{cat(b.key)}</span>
                  <span className={cn(s.budgetPct, s.num)}>{f.percent(pct)}</span>
                  <span
                    className={s.budgetBar}
                    role="progressbar"
                    aria-label={cat(b.key)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={pct}
                  >
                    <i style={{ "--v": pct / 100, "--k": i } as CSSProperties} />
                  </span>
                  <span className={cn(s.budgetAmount, s.num)}>
                    {t("preview.of", {
                      spent: f.money(b.spent, { whole: true }),
                      limit: f.money(b.limit, { whole: true }),
                    })}
                  </span>
                </li>
              );
            })}
          </ul>
          <figcaption className={s.budgetFoot}>
            <span>{t("preview.total")}</span>
            <strong className={s.num}>
              {t("preview.of", {
                spent: f.money(SPENT, { whole: true }),
                limit: f.money(LIMIT, { whole: true }),
              })}
            </strong>
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
