import { describe, expect, it } from "vitest";
import { buildSlices, type CategorySpend } from "./category-chart";

const c = (id: string, amount: string): CategorySpend => ({ id, name: id, color: "sky", amount });
const sum = (list: { percent: number }[]) => list.reduce((a, b) => a + b.percent, 0);

describe("category slices", () => {
  it("is empty without spending, and ignores anything refunded below zero", () => {
    expect(buildSlices([], "Other")).toEqual([]);
    expect(buildSlices([c("a", "0.00"), c("b", "-5.00")], "Other")).toEqual([]);
  });

  it("orders by size and adds up to exactly 100", () => {
    const slices = buildSlices([c("a", "1.00"), c("b", "1.00"), c("c", "1.00")], "Other");
    expect(sum(slices)).toBe(100);
    const big = buildSlices([c("a", "10.00"), c("b", "90.00")], "Other");
    expect(big.map((s) => s.id)).toEqual(["b", "a"]);
    expect(big.map((s) => s.percent)).toEqual([90, 10]);
  });

  it("gives one category the whole ring", () => {
    const only = buildSlices([c("a", "42.50")], "Other")[0]!;
    expect(only.share).toBe(1);
    expect(only.percent).toBe(100);
  });

  it("folds many categories into Other with their exact sum", () => {
    const many = Array.from({ length: 12 }, (_, i) => c(`c${i}`, `${(i + 1) * 10}.10`));
    const slices = buildSlices(many, "Other");
    expect(slices).toHaveLength(6);
    const other = slices.at(-1)!;
    expect(other.isOther).toBe(true);
    expect(other.name).toBe("Other");
    // c0..c6: 10.10 + 20.10 + ... + 70.10
    expect(other.amount).toBe("280.70");
    expect(sum(slices)).toBe(100);
  });

  it("folds very small categories, but never a single one", () => {
    const tiny = [c("big", "1000.00"), c("a", "1.00"), c("b", "2.00"), c("mid", "300.00")];
    const folded = buildSlices(tiny, "Other");
    expect(folded.map((s) => s.id)).toEqual(["big", "mid", "other"]);
    expect(folded[2]!.amount).toBe("3.00");
    const one = buildSlices([c("big", "1000.00"), c("a", "1.00")], "Other");
    expect(one.map((s) => s.id)).toEqual(["big", "a"]);
  });

  it("handles very large amounts without losing cents", () => {
    const many = Array.from({ length: 8 }, (_, i) => c(`c${i}`, `${900000000000 + i}.01`));
    const other = buildSlices(many, "Other").at(-1)!;
    expect(other.amount).toBe("2700000000003.03"); // c0 + c1 + c2
  });
});
