import { api } from "@/lib/api";
import type { Money } from "@/lib/money";

/** A stored daily reference rate: 1 `base` = `rate` `quote`. */
export type RateView = {
  id: string;
  base: string;
  quote: string;
  rate: string;
  rate_date: string;
  requested_date: string;
  source: "provider" | "manual";
  provider: string | null;
  upstream: string | null;
  retrieved_at: string;
  age_days: number;
  is_stale: boolean;
  /** The provider couldn't be reached; this is the newest stored rate instead. */
  fallback: boolean;
};

export type ConvertResult = {
  rate: RateView | null;
  original: Money | null;
  converted: Money | null;
};

export type EquivalentTarget = string; // "USD", "XAU_G", "XAU_OZ", "BTC", ...

export type Equivalent = {
  target: EquivalentTarget;
  unit: "currency" | "gram" | "troy_ounce" | "btc";
  status: "ok" | "unavailable";
  value: string | null;
  /** One unit of the target, in the amount's currency. */
  unit_price: string | null;
  kind: "daily_reference" | "spot" | null;
  provider: string | null;
  upstream: string | null;
  rate_date: string | null;
  as_of: string | null;
  is_stale: boolean;
};

export type Equivalents = { amount: Money; targets: EquivalentTarget[]; items: Equivalent[] };

/** One currency's ledger subtotal and its rough value in the target (display only). */
export type SpendingPart = {
  currency: string;
  spent: Money;
  status: "same" | "ok" | "unavailable";
  equivalent: Money | null;
  /** 1 target = rate (this currency). */
  rate: string | null;
  reason: string | null;
  provider: string | null;
  upstream: string | null;
  rate_date: string | null;
  retrieved_at: string | null;
  is_stale: boolean;
};

/** One side of the month, each currency exact and converted where a rate exists. */
export type MonthSide = {
  parts: SpendingPart[];
  /** Null unless every currency has a rate: never an incomplete total. */
  combined: Money | null;
  complete: boolean;
  is_stale: boolean;
};

export type SpendingEquivalent = MonthSide & {
  month: string;
  target: string;
  received: MonthSide;
};

export const ASSET_TARGETS = ["XAU_G", "XAU_OZ", "BTC"] as const;

export const fxApi = {
  /** This month's spending and income at today's reference rate in one currency. */
  spending: (params: { target: string; month?: string; refresh?: boolean }) => {
    const query = new URLSearchParams({ target: params.target });
    if (params.month) query.set("month", params.month);
    if (params.refresh) query.set("refresh", "true");
    return api<SpendingEquivalent>(`/summary/equivalent?${query}`);
  },
  /** The reference rate for a day and what an amount becomes; or a typed rate's result. */
  convert: (params: {
    from: string;
    to: string;
    amount?: string;
    date?: string;
    rate?: string;
  }) => {
    const query = new URLSearchParams({ from: params.from, to: params.to });
    if (params.amount) query.set("amount", params.amount);
    if (params.date) query.set("date", params.date);
    if (params.rate) query.set("rate", params.rate);
    return api<ConvertResult>(`/fx/convert?${query}`);
  },
  equivalents: (amount: Money, targets?: EquivalentTarget[]) => {
    const query = new URLSearchParams({ amount: amount.amount, currency: amount.currency });
    if (targets?.length) query.set("targets", targets.join(","));
    return api<Equivalents>(`/equivalents?${query}`);
  },
  targets: () => api<{ targets: EquivalentTarget[] }>("/equivalents/targets"),
  saveTargets: (targets: EquivalentTarget[]) =>
    api<{ targets: EquivalentTarget[] }>("/equivalents/targets", {
      method: "PUT",
      body: { targets },
    }),
};
