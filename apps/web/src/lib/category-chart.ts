import { addAmounts, percentOf } from "@/lib/decimal";

/** One real category's spending in one currency. */
export type CategorySpend = { id: string; name: string; color: string; amount: string };

/** One slice of the donut: a category, or "Other" holding the smallest ones. */
export type Slice = CategorySpend & {
  /** For drawing only, 0 to 1. Never shown as money. */
  share: number;
  /** Whole percent for the label; slices add up to exactly 100. */
  percent: number;
  isOther: boolean;
};

export const OTHER_ID = "other";
const MAX_SLICES = 6;
const MIN_SHARE = 0.02;

const positive = (c: CategorySpend) => Number(c.amount) > 0;

/** Whole percents that add up to 100: rounded down, then the largest remainders get the rest. */
function wholePercents(shares: number[]): number[] {
  const exact = shares.map((s) => s * 100);
  const out = exact.map(Math.floor);
  const short = 100 - out.reduce((a, b) => a + b, 0);
  exact
    .map((value, i) => ({ i, rest: value - Math.floor(value) }))
    .sort((a, b) => b.rest - a.rest)
    .slice(0, Math.max(short, 0))
    .forEach(({ i }) => {
      out[i] = (out[i] ?? 0) + 1;
    });
  return out;
}

/**
 * The biggest categories as slices and the rest folded into "Other", so the chart stays readable
 * however many there are. Other's amount is the exact sum of what it holds; nothing is rounded or
 * converted, and every category stays listed in the full table beside the chart.
 */
export function buildSlices(categories: CategorySpend[], otherName: string): Slice[] {
  const sorted = categories.filter(positive).sort((a, b) => Number(b.amount) - Number(a.amount));
  if (sorted.length === 0) return [];
  const total = addAmounts(sorted.map((c) => c.amount));
  const shareOf = (amount: string) => (percentOf(amount, total) ?? 0) / 100;

  let kept = sorted;
  let folded: CategorySpend[] = [];
  const small = sorted.findIndex((c, i) => i > 0 && shareOf(c.amount) < MIN_SHARE);
  const limit = Math.min(MAX_SLICES - 1, small >= 0 ? small : sorted.length);
  if (sorted.length > MAX_SLICES || sorted.length - limit > 1) {
    kept = sorted.slice(0, limit);
    folded = sorted.slice(limit);
  }
  // Folding a single category would only rename it.
  if (folded.length === 1) {
    kept = sorted;
    folded = [];
  }
  const rows = folded.length
    ? [
        ...kept,
        {
          id: OTHER_ID,
          name: otherName,
          color: "other",
          amount: addAmounts(folded.map((c) => c.amount)),
        },
      ]
    : kept;
  const shares = rows.map((c) => shareOf(c.amount));
  const percents = wholePercents(shares);
  return rows.map((c, i) => ({
    ...c,
    share: shares[i] ?? 0,
    percent: percents[i] ?? 0,
    isOther: c.id === OTHER_ID,
  }));
}
