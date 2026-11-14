"use client";

import { Briefcase, Bus, House, ShoppingBasket } from "lucide-react";
import { useTranslations } from "next-intl";
import { type CSSProperties, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { useFormatters } from "./format";
import { STORY_START_BALANCE } from "./data";
import s from "./landing.module.css";
import { prefersReducedMotion, useReveal, useScrollProgress } from "./motion";

const PANELS = ["income", "expenses", "insights", "planning"] as const;
const BARS = [
  { key: "rent", v: 1 },
  { key: "groceries", v: 0.43 },
  { key: "eating", v: 0.2 },
  { key: "transport", v: 0.1 },
] as const;
const PLAN = [
  { key: "groceries", amount: 430 },
  { key: "eating", amount: 160 },
  { key: "transport", amount: 110 },
] as const;

/**
 * A pinned section whose panels travel sideways as the page scrolls down. Vertical scroll is never
 * intercepted: the section is exactly as tall as the horizontal distance, so one pixel down moves
 * the track one pixel across. Below 768px, and with reduced motion, the panels simply stack.
 */
export function Journey() {
  const t = useTranslations("landing.journey");
  const landing = useTranslations("landing");
  const cat = useTranslations("landing.cat");
  const f = useFormatters();
  const ref = useRef<HTMLElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLDivElement>(null);
  useScrollProgress(ref);
  useReveal(ref, 0.05);

  useEffect(() => {
    const section = ref.current;
    const track = trackRef.current;
    const rail = railRef.current;
    if (!section || !track || !rail) return;
    const wide = window.matchMedia("(min-width: 768px)");
    const measure = () => {
      if (!wide.matches || prefersReducedMotion()) {
        section.style.removeProperty("block-size");
        section.style.removeProperty("--dist");
        return;
      }
      // The rail's own transform doesn't change its scrollWidth, so this is stable mid-scroll.
      const dist = Math.max(0, rail.scrollWidth - track.clientWidth);
      section.style.setProperty("--dist", String(dist));
      section.style.blockSize = `${window.innerHeight + dist}px`;
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(rail);
    wide.addEventListener("change", measure);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      wide.removeEventListener("change", measure);
      window.removeEventListener("resize", measure);
    };
  }, []);

  return (
    <section
      data-surface="page"
      ref={ref}
      id="journey"
      className={s.journey}
      aria-labelledby="journey-title"
    >
      <div className={s.journeySticky}>
        <div className={cn(s.wrap, s.journeyHead)}>
          <h2 id="journey-title">{t("title")}</h2>
          <ol className={s.journeySteps} aria-hidden="true">
            {PANELS.map((key, i) => (
              <li key={key} style={{ "--i": i } as CSSProperties}>
                <span>{t(`${key}.step`)}</span>
                <i />
              </li>
            ))}
          </ol>
        </div>

        <div ref={trackRef} className={s.journeyTrack}>
          <div ref={railRef} className={s.journeyRail}>
            <article className={cn(s.jPanel, s.jIncome)} style={{ "--i": 0 } as CSSProperties}>
              <div className={s.jCopy}>
                <h3>{t("income.title")}</h3>
                <p>{t("income.body")}</p>
              </div>
              <div className={s.jVisual} aria-hidden="true">
                <div className={s.jIncomeCard}>
                  <span className={cn(s.dot, s.dotIn)}>
                    <Briefcase />
                  </span>
                  <span>{t("income.label")}</span>
                  <strong className={s.num}>{f.money(2900, { sign: true })}</strong>
                </div>
                {/* Where the salary landed, and the balance it leaves: the month's clear number. */}
                <dl className={s.jIncomeFacts}>
                  <div>
                    <dt>{landing("record.demo.account")}</dt>
                    <dd>
                      {landing("record.demo.bank")}
                      <small>{f.date(9, 1)}</small>
                    </dd>
                  </div>
                  <div>
                    <dt>{landing("hero.preview.balance")}</dt>
                    <dd>
                      <strong className={s.num}>{f.money(STORY_START_BALANCE + 2900)}</strong>
                      <small>
                        {landing("story.startNote", { amount: f.money(STORY_START_BALANCE) })}
                      </small>
                    </dd>
                  </div>
                </dl>
              </div>
            </article>

            <article className={cn(s.jPanel, s.jExpenses)} style={{ "--i": 1 } as CSSProperties}>
              <div className={s.jCopy}>
                <h3>{t("expenses.title")}</h3>
                <p>{t("expenses.body")}</p>
              </div>
              <ul className={s.jReceipts} aria-hidden="true">
                {(
                  [
                    { icon: House, key: "rent", amount: -950 },
                    { icon: ShoppingBasket, key: "groceries", amount: -42.3 },
                    { icon: Bus, key: "transport", amount: -20 },
                  ] as const
                ).map(({ icon: Icon, key, amount }, i) => (
                  <li key={key} style={{ "--k": i } as CSSProperties}>
                    <span className={cn(s.dot, s.dotOut)}>
                      <Icon />
                    </span>
                    <span>{cat(key)}</span>
                    <strong className={s.num}>{f.money(amount)}</strong>
                  </li>
                ))}
              </ul>
            </article>

            <article className={cn(s.jPanel, s.jInsights)} style={{ "--i": 2 } as CSSProperties}>
              <div className={s.jCopy}>
                <h3>{t("insights.title")}</h3>
                <p>{t("insights.body")}</p>
              </div>
              <div className={s.jBars} aria-hidden="true">
                {BARS.map((b, i) => (
                  <div key={b.key} style={{ "--v": b.v, "--k": i } as CSSProperties}>
                    <span>{cat(b.key)}</span>
                    <i />
                  </div>
                ))}
              </div>
            </article>

            <article className={cn(s.jPanel, s.jPlanning)} style={{ "--i": 3 } as CSSProperties}>
              <div className={s.jCopy}>
                <h3>{t("planning.title")}</h3>
                <p>{t("planning.body")}</p>
              </div>
              <ul className={s.jPlan} aria-hidden="true">
                {PLAN.map((p, i) => (
                  <li key={p.key} style={{ "--k": i } as CSSProperties}>
                    <span>{cat(p.key)}</span>
                    <strong className={s.num}>{f.money(p.amount, { whole: true })}</strong>
                  </li>
                ))}
              </ul>
            </article>
          </div>
        </div>
        <p className={cn(s.wrap, s.example)}>{t("example")}</p>
      </div>
    </section>
  );
}
