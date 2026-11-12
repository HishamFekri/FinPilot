"use client";

import { useTranslations } from "next-intl";
import { type CSSProperties, useCallback, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { STORY_EVENTS, STORY_START_BALANCE } from "./data";
import { useFormatters } from "./format";
import s from "./landing.module.css";
import { prefersReducedMotion, tweenNumber, useScrollProgress } from "./motion";

// Stops on a 1000 × 430 map. Cards sit above or below their stop, alternating.
const STOPS = [
  { x: 160, y: 232, side: "below" },
  { x: 330, y: 168, side: "above" },
  { x: 520, y: 244, side: "below" },
  { x: 700, y: 160, side: "above" },
  { x: 860, y: 240, side: "below" },
] as const;
const PATH =
  "M 30 300 C 90 300, 110 232, 160 232 S 270 168, 330 168 S 460 244, 520 244 S 640 160, 700 160 S 800 240, 860 240 S 940 140, 975 140";
// The line reaches the end at p = 1 / DRAW, then holds while the reader takes in the last card.
const DRAW = 1.12;
const INCOME = STORY_EVENTS.filter((e) => e.amount > 0).reduce((a, e) => a + e.amount, 0);

export function Story() {
  const t = useTranslations("landing.story");
  const f = useFormatters();
  const sectionRef = useRef<HTMLElement>(null);
  const pathRef = useRef<SVGPathElement>(null);
  const balanceRef = useRef<HTMLElement>(null);
  const inRef = useRef<HTMLElement>(null);
  const outRef = useRef<HTMLElement>(null);
  const inBarRef = useRef<HTMLElement>(null);
  const outBarRef = useRef<HTMLElement>(null);
  const atRef = useRef<number[]>(STOPS.map((stop) => (stop.x - 30) / 945 / DRAW));
  const reachedRef = useRef<number>(STORY_EVENTS.length);
  const cancelRef = useRef<() => void>(() => {});

  const showReached = useCallback(
    (count: number, instant = false) => {
      if (count === reachedRef.current) return;
      const before = STORY_EVENTS.slice(0, reachedRef.current).reduce(
        (a, e) => a + e.amount,
        STORY_START_BALANCE,
      );
      reachedRef.current = count;
      const passed = STORY_EVENTS.slice(0, count);
      const moneyIn = passed.filter((e) => e.amount > 0).reduce((a, e) => a + e.amount, 0);
      const moneyOut = -passed.filter((e) => e.amount < 0).reduce((a, e) => a + e.amount, 0);
      const balance = STORY_START_BALANCE + moneyIn - moneyOut;
      if (inRef.current) inRef.current.textContent = f.money(moneyIn);
      if (outRef.current) outRef.current.textContent = f.money(moneyOut);
      if (inBarRef.current) inBarRef.current.style.transform = `scaleX(${moneyIn / INCOME})`;
      if (outBarRef.current) outBarRef.current.style.transform = `scaleX(${moneyOut / INCOME})`;
      cancelRef.current();
      if (!balanceRef.current) return;
      if (instant) balanceRef.current.textContent = f.money(balance);
      else
        cancelRef.current = tweenNumber(
          balanceRef.current,
          before,
          balance,
          (n) => f.money(n),
          600,
        );
    },
    [f],
  );

  // Place each stop's threshold where the drawn line actually reaches it.
  useEffect(() => {
    const path = pathRef.current;
    const section = sectionRef.current;
    if (!path || !section) return;
    const total = path.getTotalLength();
    const at = STOPS.map((stop) => {
      let best = 0;
      let bestDist = Infinity;
      for (let l = 0; l <= total; l += total / 400) {
        const pt = path.getPointAtLength(l);
        const d = Math.hypot(pt.x - stop.x, pt.y - stop.y);
        if (d < bestDist) {
          bestDist = d;
          best = l;
        }
      }
      return best / total / DRAW;
    });
    atRef.current = at;
    section.querySelectorAll<HTMLElement>("[data-at]").forEach((el) => {
      el.style.setProperty("--at", String(at[Number(el.dataset.at)]));
    });
    // Start from the beginning of the month, unless motion is reduced.
    if (!prefersReducedMotion()) showReached(0, true);
  }, [showReached]);

  // Wide screens: progress through the tall section drives everything.
  const onProgress = useCallback(
    (p: number) => {
      if (window.matchMedia("(max-width: 899px)").matches) return;
      showReached(atRef.current.filter((a) => p >= a).length);
    },
    [showReached],
  );
  useScrollProgress(sectionRef, onProgress);

  // Narrow screens: a vertical route. Each stop arrives as it scrolls into view.
  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;
    const items = [...section.querySelectorAll<HTMLElement>("[data-event]")];
    if (prefersReducedMotion()) {
      items.forEach((el) => (el.dataset.in = "true"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (!window.matchMedia("(max-width: 899px)").matches) return;
        for (const entry of entries) {
          if (entry.isIntersecting) (entry.target as HTMLElement).dataset.in = "true";
        }
        showReached(items.filter((el) => el.dataset.in === "true").length);
      },
      { rootMargin: "0px 0px -15% 0px" },
    );
    items.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [showReached]);

  const finalIn = INCOME;
  const finalOut = -STORY_EVENTS.filter((e) => e.amount < 0).reduce((a, e) => a + e.amount, 0);

  return (
    <section
      data-surface="page"
      ref={sectionRef}
      id="story"
      className={s.story}
      aria-labelledby="story-title"
    >
      <div className={cn(s.wrap, s.storySticky)}>
        <header className={s.sectionHead}>
          <h2 id="story-title">{t("title")}</h2>
          <p className={s.lede}>{t("lede")}</p>
        </header>

        <div className={s.storyBody}>
          <div className={s.ledger}>
            <div>
              <p className={s.ledgerLabel}>{t("balance")}</p>
              <strong ref={balanceRef} className={cn(s.ledgerValue, s.num)} aria-live="off">
                {f.money(finalIn - finalOut + STORY_START_BALANCE)}
              </strong>
            </div>
            <div className={s.flow}>
              <div className={cn(s.flowRow, s.flowIn)}>
                <span>{t("in")}</span>
                <strong ref={inRef} className={s.num}>
                  {f.money(finalIn)}
                </strong>
                <span className={s.flowBar} aria-hidden="true">
                  <i ref={inBarRef} style={{ transform: "scaleX(1)" }} />
                </span>
              </div>
              <div className={cn(s.flowRow, s.flowOut)}>
                <span>{t("out")}</span>
                <strong ref={outRef} className={s.num}>
                  {f.money(finalOut)}
                </strong>
                <span className={s.flowBar} aria-hidden="true">
                  <i ref={outBarRef} style={{ transform: `scaleX(${finalOut / INCOME})` }} />
                </span>
              </div>
            </div>
            <p className={s.example}>{t("example")}</p>
            <p className={s.eventMeta}>
              {t("startNote", { amount: f.money(STORY_START_BALANCE) })}
            </p>
          </div>

          <div className={s.map}>
            <svg className={s.mapSvg} viewBox="0 0 1000 430" aria-hidden="true">
              <path className={s.mapRoad} d={PATH} />
              <path
                ref={pathRef}
                className={s.mapLine}
                d={PATH}
                pathLength={1}
                style={{ strokeDashoffset: `calc(1 - min(1, var(--p) * ${DRAW}))` }}
              />
              <circle className={s.mapStopBase} cx="30" cy="300" r="10" />
              {STOPS.map((stop, i) => (
                <g key={i}>
                  <circle className={s.mapStopBase} cx={stop.x} cy={stop.y} r="13" />
                  <circle
                    className={cn(s.mapStop, STORY_EVENTS[i]!.amount > 0 && s.mapStopIn)}
                    cx={stop.x}
                    cy={stop.y}
                    r="13"
                    data-at={i}
                    style={{ "--at": String((stop.x - 30) / 945 / DRAW) } as CSSProperties}
                  />
                </g>
              ))}
              <circle className={s.mapStopBase} cx="975" cy="140" r="10" />
            </svg>
            <ol className={s.events}>
              {STORY_EVENTS.map((event, i) => {
                const stop = STOPS[i]!;
                return (
                  <li
                    key={event.key}
                    data-event=""
                    data-at={i}
                    className={cn(s.event, stop.side === "above" ? s.eventAbove : s.eventBelow)}
                    style={
                      {
                        "--x": String(stop.x / 10),
                        "--y": String((stop.y / 430) * 100),
                        "--at": String((stop.x - 30) / 945 / DRAW),
                      } as CSSProperties
                    }
                  >
                    <p className={s.eventTitle}>{t(`events.${event.key}.title`)}</p>
                    <p className={s.eventMeta}>
                      {t("eventMeta", {
                        date: f.date(9, event.day, { day: "numeric", month: "long" }),
                        note: t(`events.${event.key}.note`),
                      })}
                    </p>
                    <p className={cn(s.eventAmount, s.num, event.amount > 0 && s.eventIn)}>
                      {f.money(event.amount, { sign: true })}
                    </p>
                  </li>
                );
              })}
            </ol>
          </div>
        </div>
        <p className={s.storyEnd}>{t("end")}</p>
      </div>
    </section>
  );
}
