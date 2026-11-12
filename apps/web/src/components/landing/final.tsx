"use client";

import { useTranslations } from "next-intl";
import { useRef } from "react";
import { cn } from "@/lib/utils";
import s from "./landing.module.css";
import { useReveal } from "./motion";
import { GoogleButton } from "./shared";
import { SiteFooter } from "./site-footer";

/** The last section of the page: the closing call to action, with the footer along its bottom. */
export function FinalCta() {
  const t = useTranslations("landing.final");
  const ref = useRef<HTMLElement>(null);
  useReveal(ref, 0.3);

  return (
    <section data-surface="tang" ref={ref} className={s.final} aria-labelledby="final-title">
      <svg
        className={s.finalRoute}
        viewBox="0 0 1440 400"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d="M -20 392 C 300 386, 520 340, 760 360 S 1080 392, 1250 262" pathLength={1} />
        <g className={s.finalFlag}>
          <line
            x1="1250"
            y1="262"
            x2="1250"
            y2="196"
            stroke="currentColor"
            strokeWidth="5"
            strokeLinecap="round"
          />
          <path
            d="M1250 196 l46 16 l-46 16 z"
            fill="currentColor"
            style={{ fill: "currentColor", stroke: "none", opacity: 1 }}
          />
        </g>
      </svg>
      <div className={cn(s.wrap, s.finalInner)}>
        <h2 id="final-title">
          <span className={s.finalLine}>{t("line1")}</span>
          <span className={s.finalLine}>{t("line2")}</span>
          <span className={s.finalLine}>{t("line3")}</span>
        </h2>
        <p className={s.finalBody}>{t("body")}</p>
        <GoogleButton />
      </div>
      <SiteFooter />
    </section>
  );
}
