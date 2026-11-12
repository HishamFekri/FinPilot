import { describe, expect, it } from "vitest";
import ar from "../../messages/ar.json";
import en from "../../messages/en.json";
import tr from "../../messages/tr.json";
import { getDirection, localeFromHeader, localeNames, routing } from "./routing";

function keys(value: object, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) =>
    typeof child === "object" && child !== null
      ? keys(child, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

function values(value: object): string[] {
  return Object.values(value).flatMap((child) =>
    typeof child === "object" && child !== null ? values(child) : [String(child)],
  );
}

describe("routing", () => {
  it("supports exactly the three confirmed languages", () => {
    expect([...routing.locales].sort()).toEqual(["ar", "en", "tr"]);
  });

  it("uses right-to-left only for Arabic", () => {
    expect(getDirection("ar")).toBe("rtl");
    expect(getDirection("en")).toBe("ltr");
    expect(getDirection("tr")).toBe("ltr");
  });

  it("only accepts known locales from a header", () => {
    expect(localeFromHeader("ar")).toBe("ar");
    expect(localeFromHeader("xx")).toBe("en");
    expect(localeFromHeader('"><script>')).toBe("en");
    expect(localeFromHeader(null)).toBe("en");
  });

  it("names every language", () => {
    expect(Object.keys(localeNames).sort()).toEqual(["ar", "en", "tr"]);
  });
});

describe("translation catalogs", () => {
  it.each([
    ["ar", ar],
    ["tr", tr],
  ])("%s has exactly the same keys as English", (_, catalog) => {
    expect(keys(catalog).sort()).toEqual(keys(en).sort());
  });

  it.each([
    ["en", en],
    ["ar", ar],
    ["tr", tr],
  ])("%s has no empty strings", (_, catalog) => {
    expect(values(catalog).filter((text) => text.trim() === "")).toEqual([]);
  });
});
