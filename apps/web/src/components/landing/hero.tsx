"use client";

import { Briefcase, House, ShoppingBasket } from "lucide-react";
import { useTranslations } from "next-intl";
import { type CSSProperties, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { STORY_EVENTS, STORY_START_BALANCE } from "./data";
import { useFormatters } from "./format";
import s from "./landing.module.css";
import { prefersReducedMotion, tweenNumber } from "./motion";
import { GoogleButton } from "./shared";

const HERO_PATH = "M -80 870 C 260 850, 470 890, 700 838 S 1080 800, 1270 826 S 1420 610, 1540 520";
const END_BALANCE = STORY_EVENTS.reduce((sum, e) => sum + e.amount, STORY_START_BALANCE);
// The month so far, in the preview's two figures: everything that came in, everything that went.
const MONEY_IN = STORY_EVENTS.reduce((sum, e) => sum + Math.max(e.amount, 0), 0);
const MONEY_OUT = STORY_EVENTS.reduce((sum, e) => sum - Math.min(e.amount, 0), 0);
const SPENT_SHARE = Math.round((MONEY_OUT / MONEY_IN) * 100);

export function Hero() {
  const t = useTranslations("landing");
  const f = useFormatters();
  const heroRef = useRef<HTMLElement>(null);
  const routeRef = useRef<SVGGElement>(null);
  const floatRef = useRef<HTMLDivElement>(null);
  const deviceRef = useRef<HTMLDivElement>(null);
  const balanceRef = useRef<HTMLElement>(null);

  // The preview's balance counts up once, as its route draws. The server renders the final value.
  useEffect(() => {
    const el = balanceRef.current;
    if (!el || prefersReducedMotion()) return;
    let cancel = () => {};
    el.textContent = f.money(STORY_START_BALANCE);
    const timer = window.setTimeout(() => {
      cancel = tweenNumber(el, STORY_START_BALANCE, END_BALANCE, (n) => f.money(n), 1400);
    }, 700);
    return () => {
      window.clearTimeout(timer);
      cancel();
    };
  }, [f]);

  // Depth on scroll: the route drifts slower than the page, and the tilted preview settles flat.
  useEffect(() => {
    const hero = heroRef.current;
    if (!hero) return;
    const reduced = prefersReducedMotion();
    const wide = window.matchMedia("(min-width: 640px)");
    const dir = document.documentElement.dir === "rtl" ? -1 : 1;
    let frame = 0;
    const update = () => {
      frame = 0;
      const y = window.scrollY;
      hero.dataset.scrolled = String(y > 60);
      if (reduced || y > window.innerHeight * 1.3) return;
      if (routeRef.current) routeRef.current.style.transform = `translateY(${y * 0.22}px)`;
      if (floatRef.current) floatRef.current.style.transform = `translateY(${y * -0.07}px)`;
      if (deviceRef.current && wide.matches) {
        const k = Math.min(1, y / 420);
        deviceRef.current.style.transform = `rotateY(${dir * -9 * (1 - k)}deg) rotateX(${4 * (1 - k)}deg)`;
      }
    };
    const request = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", request, { passive: true });
    return () => {
      window.removeEventListener("scroll", request);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  const tx = [
    { icon: Briefcase, label: t("story.events.salary.title"), day: 1, amount: 2900, tone: s.dotIn },
    { icon: House, label: t("story.events.rent.title"), day: 2, amount: -950, tone: s.dotOut },
    {
      icon: ShoppingBasket,
      label: t("story.events.groceries.title"),
      day: 9,
      amount: -412.8,
      tone: s.dotOut,
    },
  ];

  return (
    <section
      data-surface="field"
      ref={heroRef}
      id="top"
      className={s.hero}
      aria-labelledby="hero-title"
      data-scrolled="false"
    >
      <svg
        className={s.heroRoute}
        viewBox="0 0 1440 900"
        preserveAspectRatio="xMidYMid slice"
        aria-hidden="true"
      >
        <g ref={routeRef}>
          <path className={s.routeGhost} d={HERO_PATH} />
          <path className={s.routeLine} d={HERO_PATH} pathLength={1} />
          <circle
            className={s.routeStop}
            cx="700"
            cy="838"
            r="11"
            style={{ animationDelay: "900ms" }}
          />
          <circle
            className={s.routeStop}
            cx="1270"
            cy="826"
            r="11"
            style={{ animationDelay: "1400ms" }}
          />
          <circle
            className={s.routeStop}
            cx="1440"
            cy="598"
            r="14"
            style={{ animationDelay: "1850ms" }}
          />
        </g>
      </svg>

      <div className={cn(s.wrap, s.heroInner)}>
        <div>
          <h1 id="hero-title" className={s.heroTitle}>
            {t("hero.title")}
          </h1>
          <p className={s.heroLede}>{t("hero.lede")}</p>
          <div className={s.heroCtas}>
            <GoogleButton />
            <a className={cn(s.btn, s.ghost)} href="#story">
              {t("hero.how")}
            </a>
          </div>
          <p className={s.heroAssure}>{t("hero.assure")}</p>
        </div>

        <figure className={s.preview} aria-labelledby="hero-preview-caption">
          <div ref={floatRef}>
            <div ref={deviceRef} className={s.device}>
              <div className={s.deviceTop}>
                <span className={s.deviceMonth}>{f.monthName(9)}</span>
                <span className={s.example}>{t("example")}</span>
              </div>
              <p className={s.deviceBalance}>
                <span>{t("hero.preview.balance")}</span>
                <strong ref={balanceRef} className={s.num}>
                  {f.money(END_BALANCE)}
                </strong>
              </p>
              <dl className={s.deviceStats}>
                <div>
                  <dt>{t("story.in")}</dt>
                  <dd className={cn(s.num, s.amountIn)}>{f.money(MONEY_IN, { sign: true })}</dd>
                </div>
                <div>
                  <dt>{t("story.out")}</dt>
                  <dd className={s.num}>{f.money(-MONEY_OUT, { sign: true })}</dd>
                </div>
                {/* How much of the month's income has gone out: a picture of the two figures. */}
                <span className={s.deviceSpent} aria-hidden="true">
                  <i style={{ "--spent": `${SPENT_SHARE}%` } as CSSProperties} />
                </span>
              </dl>
              <ul className={s.deviceList}>
                {tx.map(({ icon: Icon, label, day, amount, tone }) => (
                  <li key={label}>
                    <span className={cn(s.dot, tone)}>
                      <Icon aria-hidden="true" />
                    </span>
                    <span>
                      {label}
                      <br />
                      <small className={s.eventMeta}>{f.date(9, day)}</small>
                    </span>
                    <span className={cn(s.amount, s.num, amount > 0 && s.amountIn)}>
                      {f.money(amount, { sign: true })}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <figcaption id="hero-preview-caption" className={s.previewCaption}>
            {t("hero.preview.caption")}
          </figcaption>
        </figure>
      </div>

      <a className={s.cue} href="#story">
        <span className={s.cueTrack} aria-hidden="true">
          <i />
        </span>
        {t("hero.scroll")}
      </a>
    </section>
  );
}
