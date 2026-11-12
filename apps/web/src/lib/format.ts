import type { Locale } from "@/i18n/routing";

/**
 * Dates follow the language but always use the Gregorian calendar and Western digits
 * (ARCHITECTURE.md §14.5, §14.6), in the user's own time zone, which is independent of language.
 */
export function localeTag(locale: Locale): string {
  return `${locale}-u-ca-gregory-nu-latn`;
}

function safeZone(timeZone: string | undefined): string | undefined {
  if (!timeZone) return undefined;
  try {
    new Intl.DateTimeFormat("en", { timeZone });
    return timeZone;
  } catch {
    return undefined;
  }
}

export function formatDateTime(iso: string, locale: Locale, timeZone?: string): string {
  return new Intl.DateTimeFormat(localeTag(locale), {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: safeZone(timeZone),
  }).format(new Date(iso));
}

export function formatDate(iso: string, locale: Locale, timeZone?: string): string {
  return new Intl.DateTimeFormat(localeTag(locale), {
    dateStyle: "long",
    timeZone: safeZone(timeZone),
  }).format(new Date(iso));
}

/** Every ISO 4217 code the browser knows, with its name in the reader's language. */
export function currencyOptions(locale: Locale): { code: string; name: string }[] {
  const codes =
    typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("currency") : [];
  const names = new Intl.DisplayNames([localeTag(locale)], { type: "currency" });
  return codes.map((code) => ({ code, name: names.of(code) ?? code }));
}

/** IANA time zones the browser knows. The API checks each one against its own list as well. */
export function timeZoneOptions(): string[] {
  return typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
}

export function deviceTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/** A time zone ID for display: "America/Argentina/Buenos_Aires" → "America/Argentina/Buenos Aires",
 *  able to wrap after each "/" rather than mid-word. */
export function timeZoneLabel(zone: string): string {
  return zone.replaceAll("_", " ").replaceAll("/", "/\u200B");
}
