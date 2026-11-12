import { api } from "@/lib/api";
import type { Money } from "@/lib/money";

/** The account types offered in the app. The API also accepts "other", which isn't offered yet. */
export const ACCOUNT_TYPES = ["cash", "bank", "wallet", "savings"] as const;
export type AccountType = (typeof ACCOUNT_TYPES)[number] | "other";

export type Account = {
  id: string;
  /** Null for the automatic Cash and Bank accounts until renamed: translate `system_key`. */
  name: string | null;
  system_key: "cash" | "bank" | null;
  is_default: boolean;
  description: string | null;
  type: AccountType;
  currency: string;
  balance: Money;
  status: "active" | "archived";
  opened_on: string;
  version: number;
};

export type CurrencyTotal = { balance: Money; accounts: number };
export type CurrencyInfo = { code: string; exponent: number };

export type ActivityItem = {
  id: string;
  type:
    | "opening_balance"
    | "adjustment"
    | "transfer"
    | "income"
    | "expense"
    | "debt_start"
    | "debt_payment"
    | "trip_expense_payment"
    | "trip_settlement_paid"
    | "trip_settlement_received"
    | "refund";
  effective_date: string;
  amount: Money;
  other_account_id: string | null;
  description: string | null;
  notes: string | null;
  recorded_at: string;
};

export type AdjustmentPreview = {
  current: Money;
  target: Money;
  difference: Money;
  direction: "increase" | "decrease" | "none";
};

export type TransferPreview = {
  sent: Money;
  received: Money;
  cross_currency: boolean;
  implied_rate: string | null;
};

/** A new key per user action, reused if the same action is retried (FINANCIAL_RULES.md §14.1). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

const once = (key: string) => ({ "Idempotency-Key": key });

export const accountsApi = {
  list: (status: "active" | "archived") =>
    api<Account[]>(`/accounts?status=${encodeURIComponent(status)}`),
  get: (id: string) => api<Account>(`/accounts/${encodeURIComponent(id)}`),
  totals: () => api<CurrencyTotal[]>("/accounts/totals"),
  currencies: () => api<CurrencyInfo[]>("/currencies"),
  activity: (id: string, limit = 50) =>
    api<{ items: ActivityItem[]; has_more: boolean }>(
      `/accounts/${encodeURIComponent(id)}/activity?limit=${limit}`,
    ),

  create: (
    body: {
      name: string;
      type: AccountType;
      currency: string;
      description?: string | null;
      opened_on?: string;
      opening_balance?: Money;
    },
    key: string,
  ) => api<Account>("/accounts", { method: "POST", body, headers: once(key) }),

  update: (id: string, version: number, changes: { name?: string; description?: string | null }) =>
    api<Account>(`/accounts/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: changes,
      headers: { "If-Match": `"${version}"` },
    }),

  makeDefault: (id: string) =>
    api<Account>(`/accounts/${encodeURIComponent(id)}/make-default`, { method: "POST" }),
  archive: (id: string) =>
    api<Account>(`/accounts/${encodeURIComponent(id)}/archive`, { method: "POST" }),
  unarchive: (id: string) =>
    api<Account>(`/accounts/${encodeURIComponent(id)}/unarchive`, { method: "POST" }),
  /** Only an account nothing was ever recorded on or uses (409 explains why otherwise). */
  remove: (id: string) => api<void>(`/accounts/${encodeURIComponent(id)}`, { method: "DELETE" }),

  previewAdjustment: (id: string, target: Money) => {
    const query = new URLSearchParams({ target: target.amount, currency: target.currency });
    return api<AdjustmentPreview>(
      `/accounts/${encodeURIComponent(id)}/adjustment-preview?${query}`,
    );
  },
  adjust: (
    id: string,
    body: { target_balance: Money; expected_balance: Money; reason?: string | null },
    key: string,
  ) =>
    api<{ id: string; difference: Money; direction: string; account: Account }>(
      `/accounts/${encodeURIComponent(id)}/adjustments`,
      { method: "POST", body, headers: once(key) },
    ),

  previewTransfer: (from: string, to: string, amount: Money, received?: Money) => {
    const query = new URLSearchParams({
      from_account_id: from,
      to_account_id: to,
      amount: amount.amount,
      currency: amount.currency,
    });
    if (received) {
      query.set("received_amount", received.amount);
      query.set("received_currency", received.currency);
    }
    return api<TransferPreview>(`/transfers/preview?${query}`);
  },
  transfer: (
    body: {
      from_account_id: string;
      to_account_id: string;
      amount: Money;
      received_amount?: Money;
      effective_date?: string;
      description?: string | null;
    },
    key: string,
  ) =>
    api<{ id: string; from_account: Account; to_account: Account }>("/transfers", {
      method: "POST",
      body,
      headers: once(key),
    }),
};
