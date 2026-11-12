import type { Money } from "@/lib/money";

/**
 * Total Balance in a chosen currency (FINANCIAL_RULES.md §7c). Display only: the API converts each
 * currency at the latest reference rate and stores nothing. A currency without a rate means no
 * combined figure.
 */

export type BalanceGroup = {
  by_currency: Money[];
  converted: (Money | null)[];
  combined: Money | null;
  complete: boolean;
};

export type AccountValue = {
  id: string;
  name: string | null;
  system_key: string | null;
  type: string;
  currency: string;
  balance: Money;
  equivalent: Money | null;
  is_default: boolean;
};

/** The display currency last chosen for totals, kept on this device only. */
const KEY = "fp_display_currency";

export function rememberedCurrency(fallback: string): string {
  try {
    const value = window.localStorage.getItem(KEY);
    return value && /^[A-Z]{3}$/.test(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

export function rememberCurrency(code: string): void {
  try {
    window.localStorage.setItem(KEY, code);
  } catch {
    /* private mode: just not remembered */
  }
}
