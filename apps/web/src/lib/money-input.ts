import type { Locale } from "@/i18n/routing";
import { parseAmount } from "@/lib/money";

/**
 * Live formatting of a money input: thousands separators appear while the person types, the caret
 * stays where it was, and nothing is ever silently misread (FINANCIAL_RULES.md M7).
 *
 * The rules:
 *
 * - **Grouping marks belong to the formatter.** They're added and moved automatically. A grouping
 *   mark the person types is refused, with a reason, rather than dropped: in Turkish "." is the
 *   thousands mark, so silently dropping it would turn "12.5" into 125.
 * - **One decimal mark**, the language's own (Arabic also accepts "\u066B"). A second one is refused.
 * - **Pasted text is parsed strictly** with the same parser as saving. If it isn't unambiguous in
 *   the language ("1,234.56" in Turkish), the paste is refused, never guessed.
 * - **Backspace or Delete on a grouping mark** removes the digit next to it, as people expect.
 * - **Intermediate states are kept**: "", "0", "12." and "12.5" stay as typed. Nothing is rounded;
 *   extra decimals stay visible so the field can say the currency doesn't have them.
 * - Arabic-Indic digits are accepted and shown as Western digits (OD-23).
 *
 * The result is display text. `parseAmount` turns it into the API's canonical string. There is no
 * number arithmetic here: only characters.
 */

type Marks = { decimal: string; decimals: string[]; group: string; groups: string[] };

export const MARKS: Record<Locale, Marks> = {
  en: { decimal: ".", decimals: ["."], group: ",", groups: [","] },
  tr: { decimal: ",", decimals: [","], group: ".", groups: ["."] },
  ar: { decimal: ".", decimals: [".", "\u066B"], group: ",", groups: [",", "\u066C"] },
};

const SPACE = /[\s\u00A0\u202F\u2009]/;
const MINUS = /[-\u2212\u2012\u2013]/;
const MAX_INTEGER_DIGITS = 16;

export type Rejection = "group" | "decimal" | "paste" | "negative" | "length";

export type EditResult = { text: string; caret: number; rejected: Rejection | null };

function digitOf(char: string): string | null {
  if (char >= "0" && char <= "9") return char;
  const code = char.charCodeAt(0);
  if (code >= 0x0660 && code <= 0x0669) return String(code - 0x0660);
  if (code >= 0x06f0 && code <= 0x06f9) return String(code - 0x06f0);
  return null;
}

/**
 * Builds the display text from any text, keeping the caret after the same significant character
 * (a digit, the decimal mark, or the minus sign). Grouping marks and spaces are recomputed.
 */
export function format(
  raw: string,
  caret: number,
  locale: Locale,
  allowNegative = false,
): { text: string; caret: number } {
  const marks = MARKS[locale];
  let negative = false;
  let whole = "";
  let fraction: string | null = null;
  let before = 0; // significant characters before the caret
  for (let i = 0; i < raw.length; i += 1) {
    const char = raw[i] ?? "";
    const digit = digitOf(char);
    let kept = false;
    if (digit !== null) {
      if (fraction === null) whole += digit;
      else fraction += digit;
      kept = true;
    } else if (marks.decimals.includes(char) && fraction === null) {
      fraction = "";
      kept = true;
    } else if (
      MINUS.test(char) &&
      allowNegative &&
      !negative &&
      whole === "" &&
      fraction === null
    ) {
      negative = true;
      kept = true;
    }
    if (kept && i < caret) before += 1;
  }

  // Leading zeros: "007" reads as "7", but "0" and "0.5" stay. The caret count follows.
  let trimmed = 0;
  while (whole.length > 1 && whole.startsWith("0")) {
    whole = whole.slice(1);
    trimmed += 1;
  }
  const leading = negative ? 1 : 0;
  before = Math.max(leading, before - Math.min(trimmed, Math.max(0, before - leading)));

  let grouped = "";
  for (let i = 0; i < whole.length; i += 1) {
    if (i > 0 && (whole.length - i) % 3 === 0) grouped += marks.group;
    grouped += whole[i];
  }
  const text =
    (negative ? "-" : "") + grouped + (fraction === null ? "" : marks.decimal + fraction);

  // Place the caret after the same number of significant characters.
  let position = 0;
  let seen = 0;
  while (position < text.length && seen < before) {
    const char = text[position] ?? "";
    if (char !== marks.group) seen += 1;
    position += 1;
  }
  return { text, caret: position };
}

/** The part of `next` that replaced part of `prev`, by common prefix and suffix. */
function diff(prev: string, next: string) {
  let start = 0;
  while (start < prev.length && start < next.length && prev[start] === next[start]) start += 1;
  let endPrev = prev.length;
  let endNext = next.length;
  while (endPrev > start && endNext > start && prev[endPrev - 1] === next[endNext - 1]) {
    endPrev -= 1;
    endNext -= 1;
  }
  return { start, removed: prev.slice(start, endPrev), inserted: next.slice(start, endNext) };
}

/** Strict reading of pasted text: the same rules as saving, then shown in the field's language. */
function readPaste(text: string, locale: Locale, allowNegative: boolean): string | null {
  const cleaned = text.trim();
  // A paste may end with a decimal mark while typing continues; the strict parser wants digits.
  const parsed = parseAmount(cleaned, locale, 20, { allowNegative });
  if (!parsed.ok) return null;
  return parsed.amount.replace(".", MARKS[locale].decimal);
}

/**
 * Applies one edit. `prev` is what the field showed; `next` and `caret` are what the browser
 * produced. `inputType` is the InputEvent's, when known ("deleteContentForward" and so on).
 */
export function applyEdit(
  prev: string,
  next: string,
  caret: number,
  locale: Locale,
  { allowNegative = false, inputType = "" }: { allowNegative?: boolean; inputType?: string } = {},
): EditResult {
  const marks = MARKS[locale];
  const { start, removed, inserted } = diff(prev, next);
  const keep = (rejected: Rejection): EditResult => ({
    text: prev,
    caret: Math.min(start + removed.length, prev.length),
    rejected,
  });

  let candidate = next;
  let candidateCaret = caret;

  if (inserted.length > 1 || inputType === "insertFromPaste" || inputType === "insertFromDrop") {
    // A paste (or autofill): strict, never guessed.
    const read = readPaste(inserted, locale, allowNegative);
    if (read === null) return keep("paste");
    candidate = prev.slice(0, start) + read + prev.slice(start + removed.length);
    candidateCaret = start + read.length;
  } else if (inserted.length === 1) {
    const char = inserted;
    if (marks.groups.includes(char) || (SPACE.test(char) && !marks.decimals.includes(char))) {
      return keep("group");
    }
    if (marks.decimals.includes(char)) {
      const rest = prev.slice(0, start) + prev.slice(start + removed.length);
      if (marks.decimals.some((mark) => rest.includes(mark))) return keep("decimal");
    } else if (MINUS.test(char)) {
      if (!allowNegative) return keep("negative");
    } else if (digitOf(char) === null) {
      // Any other character, such as a letter or the other language's decimal mark.
      return keep(Object.values(MARKS).some((m) => m.decimals.includes(char)) ? "group" : "paste");
    }
  } else if (inserted === "" && removed.length === 1 && marks.groups.includes(removed)) {
    // Backspace or Delete landed on a grouping mark: remove the digit beside it instead.
    // Backspace removes the digit before the mark; Delete, the digit after it.
    const index = inputType === "deleteContentForward" ? start + 1 : start - 1;
    if (index >= 0 && index < prev.length) {
      candidate = prev.slice(0, index) + prev.slice(index + 1);
      candidateCaret = Math.min(index, start);
    }
  }

  const result = format(candidate, candidateCaret, locale, allowNegative);
  const integerDigits = result.text.split(marks.decimal)[0]?.replace(/\D/g, "").length ?? 0;
  if (integerDigits > MAX_INTEGER_DIGITS) return keep("length");
  return { ...result, rejected: null };
}

/** The display text for a canonical amount, as the field would show it ("1234.5" → "1,234.5"). */
export { editableAmount as displayOf } from "@/lib/money";
