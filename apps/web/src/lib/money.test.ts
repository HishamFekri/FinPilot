import { describe, expect, it } from "vitest";
import { editableAmount, formatMoney, isZero, parseAmount } from "./money";

// Arabic-Indic digits and marks, built from code points so the test source stays plain ASCII.
const AR = (digits: string) =>
  digits.replace(/\d/g, (d) => String.fromCharCode(0x0660 + Number(d)));
const AR_DECIMAL = String.fromCharCode(0x066b);
const AR_GROUP = String.fromCharCode(0x066c);

describe("parseAmount (FINANCIAL_RULES.md M7)", () => {
  it.each([
    ["en", "1250.5", 2, "1250.5"],
    ["en", "1,250.50", 2, "1250.50"],
    ["en", "1,234,567.89", 2, "1234567.89"],
    ["tr", "1250,5", 2, "1250.5"],
    ["tr", "1.250,50", 2, "1250.50"],
    ["tr", "1.234", 2, "1234"], // a Turkish thousands mark, never a decimal
    ["ar", "1,250.50", 2, "1250.50"],
    ["ar", `${AR("1")}${AR_GROUP}${AR("250")}${AR_DECIMAL}${AR("50")}`, 2, "1250.50"],
    ["en", AR("42"), 2, "42"],
    ["en", " 12.5 ", 2, "12.5"],
    ["en", "0012", 2, "12"],
    ["en", "1500", 0, "1500"],
    ["en", "1.250", 3, "1.250"],
  ] as const)("%s reads %s", (locale, text, exponent, amount) => {
    expect(parseAmount(text, locale, exponent)).toEqual({ ok: true, amount });
  });

  it.each([
    ["en", "1.234", 2, "decimals"], // three decimals in English: not silently rounded
    ["en", "12.345", 2, "decimals"],
    ["en", "1.5", 0, "decimals"],
    ["tr", "1,234,5", 2, "format"],
    ["en", "1,5", 2, "format"], // a lone English comma isn't a decimal mark
    ["en", "12,34.5", 2, "format"],
    ["tr", "12.5", 2, "format"],
    ["en", "1e3", 2, "format"],
    ["en", "abc", 2, "format"],
    ["en", "", 2, "empty"],
    ["en", "-5", 2, "negative"],
    ["en", "99999999999999999", 2, "tooLarge"],
  ] as const)("%s refuses %s", (locale, text, exponent, error) => {
    expect(parseAmount(text, locale, exponent)).toEqual({ ok: false, error });
  });

  it("accepts negatives only where they make sense", () => {
    expect(parseAmount("-80.10", "en", 2, { allowNegative: true })).toEqual({
      ok: true,
      amount: "-80.10",
    });
    expect(parseAmount("-0", "en", 2, { allowNegative: true })).toEqual({ ok: true, amount: "0" });
  });
});

describe("formatMoney", () => {
  it("formats in each language without rounding", () => {
    const money = { amount: "1250.50", currency: "TRY" };
    expect(formatMoney(money, "en")).toBe("₺1,250.50");
    expect(formatMoney(money, "tr")).toBe("₺1.250,50");
    expect(formatMoney({ amount: "1250.50", currency: "EUR" }, "en")).toBe("€1,250.50");
    expect(formatMoney({ amount: "1250.50", currency: "USD" }, "en")).toBe("$1,250.50");
    // Western digits in Arabic (OD-23).
    expect(formatMoney(money, "ar")).toMatch(/1,250\.50/);
  });

  it("keeps every digit of a large amount", () => {
    expect(formatMoney({ amount: "9999999999999.99", currency: "USD" }, "en")).toBe(
      "$9,999,999,999,999.99",
    );
  });

  it("shows codes, signs, and currencies without decimals", () => {
    expect(formatMoney({ amount: "1500", currency: "JPY" }, "en")).toBe("¥1,500");
    expect(formatMoney({ amount: "-12.50", currency: "EUR" }, "en", { display: "code" })).toMatch(
      /-EUR\s12\.50/,
    );
    expect(formatMoney({ amount: "5.00", currency: "TRY" }, "en", { signed: true })).toBe("+₺5.00");
  });
});

describe("helpers", () => {
  it("prepares stored amounts for editing in the reader's language", () => {
    expect(editableAmount("1250.50", "tr")).toBe("1.250,50");
    expect(editableAmount("1234567.5", "en")).toBe("1,234,567.5");
    expect(editableAmount("-3.10", "en")).toBe("-3.10");
  });

  it("recognises zero in any form", () => {
    expect(isZero({ amount: "0.00", currency: "TRY" })).toBe(true);
    expect(isZero({ amount: "0", currency: "JPY" })).toBe(true);
    expect(isZero({ amount: "0.01", currency: "TRY" })).toBe(false);
  });
});
