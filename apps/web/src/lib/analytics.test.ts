import { describe, expect, it } from "vitest";
import { niceTicks } from "@/components/app/analytics/charts";
import { addDays, addMonths, monthEnd, periodRange, ratio, reportsApi } from "./analytics";

describe("periods", () => {
  it("works out each preset from the user's today", () => {
    // 2026-11-07 is a Saturday.
    expect(periodRange("today", "2026-11-07")).toEqual({ from: "2026-11-07", to: "2026-11-07" });
    expect(periodRange("week", "2026-11-07")).toEqual({ from: "2026-11-02", to: "2026-11-07" });
    expect(periodRange("week", "2026-11-02")).toEqual({ from: "2026-11-02", to: "2026-11-02" });
    expect(periodRange("month", "2026-11-07")).toEqual({ from: "2026-11-01", to: "2026-11-07" });
    expect(periodRange("lastMonth", "2026-03-31")).toEqual({
      from: "2026-02-01",
      to: "2026-02-28",
    });
    expect(periodRange("lastMonth", "2026-01-15")).toEqual({
      from: "2025-12-01",
      to: "2025-12-31",
    });
    expect(periodRange("all", "2026-11-07")).toEqual({ to: "2026-11-07", all_history: true });
  });

  it("does calendar arithmetic without time-zone drift", () => {
    expect(addDays("2026-03-28", 2)).toBe("2026-03-30");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(addMonths("2026-01", -1)).toBe("2025-12");
    expect(addMonths("2026-12", 1)).toBe("2027-01");
    expect(monthEnd("2028-02-10")).toBe("2028-02-29");
  });
});

describe("drawing helpers", () => {
  it("ratio is clamped and never divides by zero", () => {
    expect(ratio("50", "200")).toBe(0.25);
    expect(ratio("300", "200")).toBe(1);
    expect(ratio("5", "0")).toBe(0);
    expect(ratio("abc", "10")).toBe(0);
  });

  it("chooses round axis ticks that reach the largest value", () => {
    expect(niceTicks(52000)).toEqual([0, 20000, 40000, 60000]);
    expect(niceTicks(0)).toEqual([0, 1]);
    const ticks = niceTicks(17_500);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(17_500);
  });
});

describe("export links", () => {
  it("carry the filters and the language, and leave out empty ones", () => {
    const url = reportsApi.pdfUrl(
      { from: "2026-11-01", to: "2026-11-07", q: "", currency: "TRY", all_history: false },
      "ar",
    );
    expect(url).toBe(
      "/api/v1/reports/export.pdf?from=2026-11-01&to=2026-11-07&currency=TRY&locale=ar",
    );
    expect(reportsApi.csvUrl({ to: "2026-11-07", all_history: true }, "tr")).toBe(
      "/api/v1/reports/export.csv?to=2026-11-07&all_history=true&locale=tr",
    );
  });
});
