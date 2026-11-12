import { defineRouting } from "next-intl/routing";

// ARCHITECTURE.md §14.3: every route carries its language, as /en/..., /ar/..., or /tr/...
export const routing = defineRouting({
  locales: ["en", "ar", "tr"],
  defaultLocale: "en",
  localePrefix: "always",
  localeCookie: { name: "fp_locale", maxAge: 60 * 60 * 24 * 365 },
});

export type Locale = (typeof routing.locales)[number];

// Each language is written in its own name, and never shown as a flag.
export const localeNames: Record<Locale, string> = {
  en: "English",
  ar: "العربية",
  tr: "Türkçe",
};

export function getDirection(locale: Locale): "ltr" | "rtl" {
  return locale === "ar" ? "rtl" : "ltr";
}

/** A header can arrive from outside on paths the proxy skips, so it's validated, never trusted. */
export function localeFromHeader(value: string | null): Locale {
  return routing.locales.find((locale) => locale === value) ?? routing.defaultLocale;
}
