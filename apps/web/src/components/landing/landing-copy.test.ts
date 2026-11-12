import { describe, expect, it } from "vitest";
import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import tr from "../../../messages/tr.json";

/** The landing page must describe the product as it is (each phrase was once true or overclaimed). */
const OUTDATED = [
  /\bplanned\b/i,
  /aren't built/i,
  /\bfewest\b/i,
  /used last, and your usual/i,
  /قيد التخطيط|نخطط له|غير متاحة بعد|أقل عدد من الدفعات/,
  /planlanıyor|planlananların|henüz hazır|en az ödeme/i,
];

describe("landing page copy", () => {
  it.each([
    ["en", en],
    ["ar", ar],
    ["tr", tr],
  ])("%s says nothing outdated", (_, catalog) => {
    const text = JSON.stringify(catalog.landing);
    for (const phrase of OUTDATED) expect(text).not.toMatch(phrase);
    expect(catalog.landing.reports).not.toHaveProperty("planned");
  });

  it.each([
    ["en", en],
    ["ar", ar],
    ["tr", tr],
  ])("%s lists the same report features", (_, catalog) => {
    expect(Object.keys(catalog.landing.reports.features).sort()).toEqual([
      "export",
      "filters",
      "print",
      "summaries",
    ]);
  });
});
