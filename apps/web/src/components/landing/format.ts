"use client";

import { useLocale } from "next-intl";
import { useMemo } from "react";

// Arabic uses Western digits for now. Whether to default to Arabic-Indic digits is an open
// question for the <Money> component (PRD §7).
const INTL: Record<string, string> = { en: "en-IE", ar: "ar-u-nu-latn", tr: "tr-TR" };

export function intlLocale(locale: string): string {
  return INTL[locale] ?? "en-IE";
}

export type Formatters = {
  money: (amount: number, opts?: { whole?: boolean; sign?: boolean }) => string;
  date: (month: number, day: number, opts?: Intl.DateTimeFormatOptions) => string;
  monthName: (month: number, style?: "long" | "short") => string;
  percent: (value: number) => string;
  currencySymbol: string;
  locale: string;
};

export function makeFormatters(locale: string): Formatters {
  const tag = intlLocale(locale);
  return {
    locale: tag,
    money: (amount, opts = {}) =>
      new Intl.NumberFormat(tag, {
        style: "currency",
        currency: "EUR",
        minimumFractionDigits: opts.whole ? 0 : 2,
        maximumFractionDigits: opts.whole ? 0 : 2,
        signDisplay: opts.sign ? "exceptZero" : "auto",
      }).format(amount),
    date: (month, day, opts = { day: "numeric", month: "short" }) =>
      new Intl.DateTimeFormat(tag, { ...opts, timeZone: "UTC" }).format(Date.UTC(2026, month, day)),
    currencySymbol:
      new Intl.NumberFormat(tag, { style: "currency", currency: "EUR" })
        .formatToParts(0)
        .find((p) => p.type === "currency")?.value ?? "€",
    // Turkish writes "%65", Arabic "65٪" in its own order; Intl knows each.
    percent: (value) =>
      new Intl.NumberFormat(tag, { style: "percent", maximumFractionDigits: 0 }).format(
        value / 100,
      ),
    monthName: (month, style = "long") =>
      new Intl.DateTimeFormat(tag, { month: style, timeZone: "UTC" }).format(
        Date.UTC(2026, month, 1),
      ),
  };
}

export function useFormatters(): Formatters {
  const locale = useLocale();
  return useMemo(() => makeFormatters(locale), [locale]);
}
