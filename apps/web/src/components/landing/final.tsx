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
      <div className={cn(s.wrap, s.finalInner)}>
        <h2 id="final-title">
          <span className={s.finalLine}>{t("line1")}</span>
          <span className={s.finalLine}>{t("line2")}</span>
          <span className={s.finalLine}>{t("line3")}</span>
        </h2>
        <p className={s.finalBody}>{t("body")}</p>
        <div className={s.finalAction}>
          <GoogleButton />
          <p className={s.finalAccess}>{t("access")}</p>
        </div>
      </div>
      <SiteFooter />
    </section>
  );
}
