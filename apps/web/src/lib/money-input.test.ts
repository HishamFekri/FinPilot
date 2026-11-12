import { describe, expect, it } from "vitest";
import type { Locale } from "@/i18n/routing";
import { parseAmount } from "@/lib/money";
import { applyEdit, displayOf, format } from "@/lib/money-input";

/** Types `keys` one at a time at the end of the field, as a person would. */
function typeIn(keys: string, locale: Locale, allowNegative = false): string {
  let text = "";
  for (const key of keys) {
    const next = text + key;
    const result = applyEdit(text, next, next.length, locale, { allowNegative });
    text = result.text;
  }
  return text;
}

/** One edit at a caret: `before|after` notation, `|` marking the caret in the input and output. */
function edit(
  field: string,
  insert: string,
  locale: Locale,
  options: { inputType?: string; selectTo?: number } = {},
) {
  const caret = field.indexOf("|");
  const prev = field.replace("|", "");
  const end = options.selectTo ?? caret;
  const next = prev.slice(0, caret) + insert + prev.slice(end);
  const result = applyEdit(prev, next, caret + insert.length, locale, options);
  return {
    shown: `${result.text.slice(0, result.caret)}|${result.text.slice(result.caret)}`,
    rejected: result.rejected,
  };
}

function backspace(field: string, locale: Locale) {
  const caret = field.indexOf("|");
  const prev = field.replace("|", "");
  const next = prev.slice(0, caret - 1) + prev.slice(caret);
  const result = applyEdit(prev, next, caret - 1, locale, { inputType: "deleteContentBackward" });
  return `${result.text.slice(0, result.caret)}|${result.text.slice(result.caret)}`;
}

function del(field: string, locale: Locale) {
  const caret = field.indexOf("|");
  const prev = field.replace("|", "");
  const next = prev.slice(0, caret) + prev.slice(caret + 1);
  const result = applyEdit(prev, next, caret, locale, { inputType: "deleteContentForward" });
  return `${result.text.slice(0, result.caret)}|${result.text.slice(result.caret)}`;
}

describe("grouping while typing", () => {
  it.each([
    ["en", "1234567.89", "1,234,567.89"],
    ["tr", "1234567,89", "1.234.567,89"],
    ["ar", "1234567.89", "1,234,567.89"],
    ["ar", "١٢٣٤٥٦٧٫٨٩", "1,234,567.89"],
    ["en", "1000000000000000", "1,000,000,000,000,000"],
  ] as const)("%s: %s → %s", (locale, keys, shown) => {
    const text = typeIn(keys, locale);
    expect(text).toBe(shown);
  });

  it("keeps every intermediate state while typing", () => {
    expect(typeIn("", "en")).toBe("");
    expect(typeIn("0", "en")).toBe("0");
    expect(typeIn("0.", "en")).toBe("0.");
    expect(typeIn("0.0", "en")).toBe("0.0");
    expect(typeIn("12.", "en")).toBe("12.");
    expect(typeIn("12,", "tr")).toBe("12,");
    expect(typeIn("12,5", "tr")).toBe("12,5");
    expect(typeIn("007", "en")).toBe("7");
  });

  it("never rounds or drops extra decimals: the field says the currency doesn't have them", () => {
    const text = typeIn("1.234", "en");
    expect(text).toBe("1.234");
    expect(parseAmount(text, "en", 2)).toEqual({ ok: false, error: "decimals" });
  });

  it("the display parses back to exactly what was typed", () => {
    expect(parseAmount(typeIn("1234567.89", "en"), "en", 2)).toEqual({
      ok: true,
      amount: "1234567.89",
    });
    expect(parseAmount(typeIn("1234567,89", "tr"), "tr", 2)).toEqual({
      ok: true,
      amount: "1234567.89",
    });
    expect(parseAmount(typeIn("12.", "en"), "en", 2)).toEqual({ ok: true, amount: "12" });
  });
});

describe("caret", () => {
  it("stays after the digit just typed when a separator appears before it", () => {
    expect(edit("123|", "4", "en").shown).toBe("1,234|");
    expect(edit("1,23|4", "5", "en").shown).toBe("12,35|4");
  });

  it("allows editing digits in the middle", () => {
    expect(edit("1,|234,567", "9", "en").shown).toBe("19|,234,567");
    expect(edit("1.2|34.567,89", "0", "tr").shown).toBe("12.0|34.567,89");
  });

  it("replaces a selection", () => {
    // "1,234,567" with "234" selected, replaced by "9".
    expect(edit("1,|234,567", "9", "en", { selectTo: 5 }).shown).toBe("19|,567");
  });

  it("backspace over a separator removes the digit before it", () => {
    expect(backspace("1,|234", "en")).toBe("|234");
    expect(backspace("12,|345", "en")).toBe("1|,345");
    expect(backspace("1.234.|567", "tr")).toBe("123|.567");
  });

  it("delete on a separator removes the digit after it", () => {
    expect(del("1|,234", "en")).toBe("1|34");
    expect(del("12|,345,678", "en")).toBe("1,2|45,678");
  });

  it("removing leading digits keeps the caret on the right digit", () => {
    expect(backspace("1|,000", "en")).toBe("|0");
  });
});

describe("never misread", () => {
  it("refuses a typed thousands mark instead of dropping it (Turkish 12.5 would become 125)", () => {
    expect(edit("12|", ".", "tr")).toEqual({ shown: "12|", rejected: "group" });
    expect(edit("12|", ",", "en")).toEqual({ shown: "12|", rejected: "group" });
    expect(edit("12|", "٬", "ar")).toEqual({ shown: "12|", rejected: "group" });
    expect(edit("12|", " ", "en")).toEqual({ shown: "12|", rejected: "group" });
  });

  it("accepts each language's decimal mark, once", () => {
    expect(edit("12|", ",", "tr").shown).toBe("12,|");
    expect(edit("12|", "٫", "ar").shown).toBe("12.|");
    expect(edit("12.5|", ".", "en").rejected).toBe("decimal");
  });

  it("refuses letters and a minus sign unless negatives are allowed", () => {
    expect(edit("12|", "a", "en").rejected).toBe("paste");
    expect(edit("|12", "-", "en").rejected).toBe("negative");
    expect(edit("|12", "-", "en", {}).shown).toBe("|12");
    expect(applyEdit("12", "-12", 1, "en", { allowNegative: true }).text).toBe("-12");
  });

  it("caps the integer part at sixteen digits", () => {
    const full = typeIn("1234567890123456", "en");
    expect(edit(`${full}|`, "7", "en").rejected).toBe("length");
  });
});

describe("pasting", () => {
  it.each([
    ["en", "1,234,567.89", "1,234,567.89"],
    ["en", "1234567.89", "1,234,567.89"],
    ["tr", "1.234.567,89", "1.234.567,89"],
    ["tr", "1234567,89", "1.234.567,89"],
    ["ar", "١٢٣٤٫٥", "1,234.5"],
    ["en", "  42  ", "42"],
  ] as const)("%s: %s → %s", (locale, pasted, shown) => {
    expect(
      applyEdit("", pasted, pasted.length, locale, { inputType: "insertFromPaste" }).text,
    ).toBe(shown);
  });

  it.each([
    ["tr", "1,234.56"],
    ["tr", "1234.56"],
    ["en", "1.234,56"],
    ["en", "12abc"],
    ["en", "1,23"],
  ] as const)("%s refuses the ambiguous %s", (locale, pasted) => {
    const result = applyEdit("5", pasted, pasted.length, locale, { inputType: "insertFromPaste" });
    expect(result).toMatchObject({ text: "5", rejected: "paste" });
  });
});

describe("display of stored amounts", () => {
  it("formats canonical strings for editing in each language", () => {
    expect(displayOf("1234567.89", "en")).toBe("1,234,567.89");
    expect(displayOf("1234567.89", "tr")).toBe("1.234.567,89");
    expect(displayOf("-2500", "en")).toBe("-2,500");
    expect(format("0012", 4, "en").text).toBe("12");
  });
});
