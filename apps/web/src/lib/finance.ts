import { api } from "@/lib/api";
import type { Money } from "@/lib/money";

export type Kind = "expense" | "income";

export type SetupState = {
  completed: boolean;
  needs_currency: boolean;
  preferred_currency: string | null;
  default_account_id: string | null;
};

export type Category = {
  /** Expense categories: their group, if any. */
  group_id?: string | null;
  id: string;
  kind: Kind;
  /** Seeded defaults: translate this, unless `name` is set. */
  system_key: string | null;
  name: string | null;
  icon: string;
  color: string;
  status: "active" | "archived";
  version: number;
  last_used_at?: string | null;
  /** Pinned first in Quick Add. */
  is_favourite?: boolean;
};

export type AccountRef = {
  id: string;
  name: string | null;
  system_key: string | null;
  currency: string;
  status: string;
};

export type TransactionType =
  | "income"
  | "expense"
  | "transfer"
  | "adjustment"
  | "opening_balance"
  | "debt_start"
  | "debt_payment"
  // Trip cash (FINANCIAL_RULES §11.3): never spending or income; the share is the spending.
  | "trip_expense_payment"
  | "trip_settlement_paid"
  | "trip_settlement_received"
  // Money back for a purchase: reduces spending, never income (FINANCIAL_RULES §4.11).
  | "refund";

/** How an amount entered in another currency became the posted amount. */
export type FxInfo = {
  method: "reference" | "manual_rate" | "actual_amount";
  /** 1 original currency = rate account currency (implied when the charged amount was typed). */
  rate: string;
  rate_date: string | null;
  source: string | null;
  provider: string | null;
  upstream: string | null;
};

/** The person's choice in the conversion panel, sent with the save. */
export type ConversionChoice =
  | { method: "reference"; rate_id: string; accept_stale?: boolean }
  | { method: "manual_rate"; rate: string }
  | { method: "actual_amount"; account_amount: string; rate_id?: string };

export type Transaction = {
  id: string;
  type: TransactionType;
  status: "posted" | "reversed";
  effective_date: string;
  amount: Money;
  direction: "increase" | "decrease" | null;
  /** Null only for a debt that existed before FinPilot and moved no money. */
  account: AccountRef | null;
  counter_account: AccountRef | null;
  counter_amount: Money | null;
  category: Category | null;
  /** Entered in another currency: what was entered (a memo, never added to totals). */
  original_amount?: Money | null;
  fx?: FxInfo | null;
  debt?: {
    id: string;
    counterparty: string;
    direction: "i_owe" | "owed_to_me";
    currency: string;
  } | null;
  debt_amount?: Money | null;
  /** A trip entry: the person's own link to the trip it came from (only they see it). */
  trip_link?: {
    link_id: string;
    trip_id: string;
    trip_name: string;
    kind: "expense_payment" | "settlement_paid" | "settlement_received";
    status: "active" | "unlinked";
  } | null;
  /** Attached or converted: the entry this one replaced (that one stays, voided). */
  replaces_transaction_id?: string | null;
  /** A refund: the purchase it refunds. */
  refunds_transaction_id?: string | null;
  description: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type TransactionDetail = Transaction & {
  changes: { at: string; action: "created" | "corrected" | "voided" }[];
  warnings?: string[];
  /** Expenses only: what came back, what's left to refund, and every refund. */
  refunds?: { refunded: Money; refundable: Money; items: Transaction[] } | null;
};

export type MonthSummary = {
  month: string;
  currencies: {
    currency: string;
    expenses: Money;
    income: Money;
    transactions: number;
    top_categories: { category: Category; amount: Money }[];
    /** Every category with spending, largest first. */
    categories?: { category: Category; amount: Money }[];
    /** Spending with no category of its own, so a breakdown adds up to `expenses`. */
    uncategorized?: Money | null;
    /** Null when nothing came from trips. `trip_shares` is inside `expenses`; the cash isn't. */
    trip_shares?: Money | null;
    trip_paid?: Money | null;
    trip_received?: Money | null;
  }[];
};

export type HistoryFilters = {
  type?: TransactionType;
  account_id?: string;
  category_id?: string;
  currency?: string;
  from?: string;
  to?: string;
  q?: string;
  include_voided?: boolean;
};

const once = (key: string) => ({ "Idempotency-Key": key });
const ifMatch = (version: number) => ({ "If-Match": `"${version}"` });
const enc = encodeURIComponent;

export const financeApi = {
  setup: () => api<SetupState>("/setup"),
  runSetup: (currency?: string) =>
    api<SetupState>("/setup", { method: "POST", body: currency ? { currency } : {} }),

  categories: (includeArchived = false) =>
    api<Category[]>(`/categories${includeArchived ? "?include_archived=true" : ""}`),
  /** Only a custom category nothing ever used (409 explains why otherwise). */
  deleteCategory: (id: string) =>
    api<void>(`/categories/${encodeURIComponent(id)}`, { method: "DELETE" }),
  setFavourite: (id: string, favourite: boolean) =>
    api<Category>(`/categories/${encodeURIComponent(id)}/favourite`, {
      method: "PUT",
      body: { favourite },
    }),
  createCategory: (body: { kind: Kind; name: string; icon: string; color: string }) =>
    api<Category>("/categories", { method: "POST", body }),
  updateCategory: (
    id: string,
    version: number,
    changes: Partial<{ name: string | null; icon: string; color: string; status: string }>,
  ) =>
    api<Category>(`/categories/${enc(id)}`, {
      method: "PATCH",
      body: changes,
      headers: ifMatch(version),
    }),

  record: (
    body: {
      type: Kind;
      account_id: string;
      amount: Money;
      category_id: string;
      effective_date?: string;
      description?: string | null;
      notes?: string | null;
      conversion?: ConversionChoice;
      source?: "quick_add" | "template";
    },
    key: string,
  ) => api<TransactionDetail>("/transactions", { method: "POST", body, headers: once(key) }),

  /** Returns the purchase with its refunds (the new one last). */
  refund: (
    id: string,
    body: {
      amount: Money;
      account_id?: string | null;
      effective_date?: string;
      description?: string | null;
      notes?: string | null;
      conversion?: ConversionChoice;
    },
    key: string,
  ) =>
    api<TransactionDetail>(`/transactions/${enc(id)}/refunds`, {
      method: "POST",
      body,
      headers: once(key),
    }),

  history: (filters: HistoryFilters, cursor?: string | null, limit = 30) => {
    const query = new URLSearchParams({ limit: String(limit) });
    for (const [name, value] of Object.entries(filters)) if (value) query.set(name, String(value));
    if (cursor) query.set("cursor", cursor);
    return api<{ items: Transaction[]; next_cursor: string | null }>(`/transactions?${query}`);
  },
  get: (id: string) => api<TransactionDetail>(`/transactions/${enc(id)}`),
  correct: (
    id: string,
    version: number,
    changes: Partial<{
      amount: Money;
      account_id: string;
      category_id: string;
      effective_date: string;
      description: string | null;
      notes: string | null;
      conversion: ConversionChoice;
    }>,
  ) =>
    api<TransactionDetail>(`/transactions/${enc(id)}`, {
      method: "PATCH",
      body: changes,
      headers: ifMatch(version),
    }),
  void: (id: string, version: number) =>
    api<TransactionDetail>(`/transactions/${enc(id)}/void`, {
      method: "POST",
      headers: ifMatch(version),
    }),

  summary: (month?: string) => api<MonthSummary>(`/summary${month ? `?month=${enc(month)}` : ""}`),
};
