import type { Locale } from "@/i18n/routing";
import { localeTag } from "@/lib/format";

/**
 * Money in the browser: parsing what people type, and displaying what the API sends.
 *
 * FINANCIAL_RULES.md M5 and M7: the browser never adds, splits, or converts money. It turns typed
 * text into the API's canonical decimal string ("1234.5"), and formats the API's strings for
 * display. Amounts stay strings; Intl formats a string exactly, without going through a float.
 */

/** `{ amount: "12450.75", currency: "TRY" }`, as the API sends and receives it. */
export type Money = { amount: string; currency: string };

type Separators = { decimal: string[]; group: string[] };

// What each language uses (Western digits in all three, OD-23). Arabic also accepts the Arabic
// decimal and thousands marks, and every language accepts Arabic-Indic digits.
const SEPARATORS: Record<Locale, Separators> = {
  en: { decimal: ["."], group: [","] },
  tr: { decimal: [","], group: ["."] },
  ar: { decimal: [".", "\u066B"], group: [",", "\u066C"] },
};

const SPACES = /[\s\u00A0\u202F\u2009]/g;
const ARABIC_INDIC = /[\u0660-\u0669]/g;
const EXTENDED_ARABIC_INDIC = /[\u06F0-\u06F9]/g;
const MAX_INTEGER_DIGITS = 16;

export type ParseResult =
  | { ok: true; amount: string }
  | { ok: false; error: "empty" | "format" | "decimals" | "negative" | "tooLarge" };

/**
 * Typed text in a language → the API's canonical form, or a reason it isn't valid.
 *
 * Never guesses: a separator the language doesn't use, misplaced thousands marks, or more
 * decimals than the currency has are errors. The caller shows the interpreted value back before
 * saving, so "1.234" in Turkish (one thousand two hundred thirty-four) can't be misread.
 */
export function parseAmount(
  text: string,
  locale: Locale,
  exponent: number,
  { allowNegative = false }: { allowNegative?: boolean } = {},
): ParseResult {
  let value = text
    .replace(SPACES, "")
    .replace(ARABIC_INDIC, (d) => String(d.charCodeAt(0) - 0x0660))
    .replace(EXTENDED_ARABIC_INDIC, (d) => String(d.charCodeAt(0) - 0x06f0))
    .replace(/[\u2212\u2012\u2013]/g, "-");
  if (!value) return { ok: false, error: "empty" };

  let negative = false;
  if (value.startsWith("-")) {
    negative = true;
    value = value.slice(1);
  }
  if (negative && !allowNegative) return { ok: false, error: "negative" };

  const { decimal, group } = SEPARATORS[locale];
  let whole = value;
  let fraction = "";
  const decimalAt = Math.max(...decimal.map((mark) => value.lastIndexOf(mark)));
  if (decimalAt >= 0) {
    whole = value.slice(0, decimalAt);
    fraction = value.slice(decimalAt + 1);
    // "12." while typing reads as 12; anything else after the mark must be digits.
    if (fraction && !/^\d+$/.test(fraction)) return { ok: false, error: "format" };
  }

  const groupMark = group.find((mark) => whole.includes(mark));
  if (groupMark !== undefined) {
    const parts = whole.split(groupMark);
    const [head, ...rest] = parts;
    if (!head || !/^\d{1,3}$/.test(head) || rest.some((p) => !/^\d{3}$/.test(p))) {
      return { ok: false, error: "format" };
    }
    whole = parts.join("");
  }
  if (!/^\d+$/.test(whole)) return { ok: false, error: "format" };
  if (fraction.length > exponent) return { ok: false, error: "decimals" };

  whole = whole.replace(/^0+(?=\d)/, "");
  if (whole.length > MAX_INTEGER_DIGITS) return { ok: false, error: "tooLarge" };
  const isZero = /^0*$/.test(whole + fraction);
  const canonical = fraction ? `${whole}.${fraction}` : whole;
  return { ok: true, amount: negative && !isZero ? `-${canonical}` : canonical };
}

/**
 * Displays an API amount in the reader's language: "₺12.450,75" in Turkish, "€1,250.50" in
 * English. `code` shows the ISO code instead of a symbol, for lists mixing currencies
 * (ARCHITECTURE.md, section 14.5).
 */
export function formatMoney(
  money: Money,
  locale: Locale,
  {
    display = "symbol",
    signed = false,
  }: { display?: "symbol" | "code" | "none"; signed?: boolean } = {},
): string {
  const fractionDigits = (money.amount.split(".")[1] ?? "").length;
  const options: Intl.NumberFormatOptions = {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
    signDisplay: signed ? "exceptZero" : "auto",
  };
  if (display !== "none") {
    options.style = "currency";
    options.currency = money.currency;
    options.currencyDisplay = display === "code" ? "code" : "narrowSymbol";
  }
  // A string, not a number: Intl formats decimal strings exactly (ES2023), so a large or precise
  // amount is never rounded on the way to the screen.
  return new Intl.NumberFormat(localeTag(locale), options).format(
    money.amount as Intl.StringNumericLiteral,
  );
}

/**
 * A canonical amount written with all of its currency's decimals: "18.5" -> "18.50". Text padding
 * only; no arithmetic (M5).
 */
export function padAmount(amount: string, exponent: number): string {
  if (exponent === 0) return amount;
  const [whole, fraction = ""] = amount.split(".");
  return `${whole}.${fraction.padEnd(exponent, "0")}`;
}

/**
 * A stored amount as the money input shows it, in the reader's language: "1,250.5" in English,
 * "1.250,5" in Turkish. The same grouping the live field applies while typing.
 */
export function editableAmount(amount: string, locale: Locale): string {
  const [whole = "0", fraction] = amount.replace(/^-/, "").split(".");
  const sign = amount.startsWith("-") ? "-" : "";
  const { decimal, group } = SEPARATORS[locale];
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, group[0] ?? ",");
  return `${sign}${grouped}${fraction ? `${decimal[0] ?? "."}${fraction}` : ""}`;
}

export function isNegative(money: Money): boolean {
  return money.amount.startsWith("-");
}

export function isZero(money: Money): boolean {
  return /^-?0+(\.0+)?$/.test(money.amount);
}
