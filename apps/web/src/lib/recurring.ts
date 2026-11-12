import { api } from "@/lib/api";
import type { AccountRef, Category, ConversionChoice, TransactionDetail } from "@/lib/finance";
import type { Money } from "@/lib/money";

export type RuleKind = "income" | "expense" | "debt_payment";
export type Frequency = "weekly" | "biweekly" | "monthly" | "yearly";

export type DebtRef = {
  id: string;
  counterparty: string;
  direction: "i_owe" | "owed_to_me";
  currency: string;
};

export type Occurrence = {
  id: string;
  rule_id: string;
  rule_name: string;
  kind: RuleKind;
  sequence: number;
  total: number | null;
  window_start: string;
  window_end: string;
  /** Null for a variable amount. */
  expected: Money | null;
  actual: Money | null;
  account: AccountRef;
  category: Category | null;
  debt: DebtRef | null;
  status: "pending" | "confirmed" | "skipped";
  state: "expected" | "pending" | "overdue" | "confirmed" | "skipped";
  remind: boolean;
  transaction_id: string | null;
  version: number;
};

export type Rule = {
  id: string;
  kind: RuleKind;
  name: string;
  notes: string | null;
  amount: Money | null;
  currency: string;
  account: AccountRef;
  category: Category | null;
  debt: DebtRef | null;
  frequency: Frequency;
  window_start_day: number | null;
  window_end_day: number | null;
  month: number | null;
  start_date: string;
  end_date: string | null;
  total_occurrences: number | null;
  remind_days_before: number;
  reminders: boolean;
  /** Shown in Subscriptions: a monthly or yearly expense, same engine. */
  is_subscription?: boolean;
  status: "active" | "paused" | "ended";
  version: number;
  next: Occurrence | null;
};

export type RuleBody = {
  kind: RuleKind;
  name: string;
  notes?: string | null;
  amount: Money | null;
  currency: string;
  account_id: string;
  category_id?: string | null;
  debt_id?: string | null;
  frequency: Frequency;
  window_start_day?: number | null;
  window_end_day?: number | null;
  month?: number | null;
  start_date?: string;
  end_date?: string | null;
  total_occurrences?: number | null;
  remind_days_before: number;
  reminders: boolean;
  is_subscription?: boolean;
};

export type Debt = {
  id: string;
  direction: "i_owe" | "owed_to_me";
  counterparty: string;
  currency: string;
  original: Money;
  remaining: Money;
  repaid: Money;
  settled: boolean;
  start_date: string;
  notes: string | null;
  status: "active" | "archived";
  version: number;
};

export type DebtDetail = Debt & { history: TransactionDetail[] };

const once = (key: string) => ({ "Idempotency-Key": key });
const ifMatch = (version: number) => ({ "If-Match": `"${version}"` });
const enc = encodeURIComponent;

export const recurringApi = {
  rules: () => api<Rule[]>("/recurring/rules"),
  createRule: (body: RuleBody, key: string) =>
    api<Rule>("/recurring/rules", { method: "POST", body, headers: once(key) }),
  updateRule: (id: string, version: number, changes: Partial<RuleBody>) =>
    api<Rule>(`/recurring/rules/${enc(id)}`, {
      method: "PATCH",
      body: changes,
      headers: ifMatch(version),
    }),
  /** Only when no payment was ever confirmed (409 `rule_has_payments` otherwise). */
  deleteRule: (id: string, version: number) =>
    api<void>(`/recurring/rules/${enc(id)}`, {
      method: "DELETE",
      headers: { "If-Match": `"${version}"` },
    }),
  setStatus: (id: string, version: number, action: "pause" | "resume" | "end") =>
    api<Rule>(`/recurring/rules/${enc(id)}/${action}`, {
      method: "POST",
      headers: ifMatch(version),
    }),
  occurrences: (params: { from?: string; to?: string; rule_id?: string } = {}) => {
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(params)) if (value) query.set(name, value);
    return api<Occurrence[]>(`/recurring/occurrences${query.size ? `?${query}` : ""}`);
  },
  upcoming: (limit = 4) => api<Occurrence[]>(`/recurring/upcoming?limit=${limit}`),
  confirm: (
    id: string,
    body: {
      amount: Money;
      account_id?: string;
      effective_date?: string;
      category_id?: string;
      description?: string | null;
      conversion?: ConversionChoice;
      account_amount?: string;
    },
    key: string,
  ) =>
    api<{ occurrence: Occurrence; transaction: TransactionDetail }>(
      `/recurring/occurrences/${enc(id)}/confirm`,
      { method: "POST", body, headers: once(key) },
    ),
  skip: (id: string) =>
    api<Occurrence>(`/recurring/occurrences/${enc(id)}/skip`, { method: "POST" }),
  notYet: (id: string) =>
    api<Occurrence>(`/recurring/occurrences/${enc(id)}/not-yet`, { method: "POST" }),

  debts: () => api<Debt[]>("/debts"),
  debt: (id: string) => api<DebtDetail>(`/debts/${enc(id)}`),
  createDebt: (
    body: {
      direction: Debt["direction"];
      counterparty: string;
      amount: Money;
      start_date?: string;
      notes?: string | null;
      account_id?: string | null;
      account_amount?: string;
    },
    key: string,
  ) => api<Debt>("/debts", { method: "POST", body, headers: once(key) }),
  pay: (
    id: string,
    body: {
      account_id: string;
      amount: Money;
      account_amount?: string;
      effective_date?: string;
      description?: string | null;
    },
    key: string,
  ) =>
    api<{ debt: Debt; transaction: TransactionDetail }>(`/debts/${enc(id)}/payments`, {
      method: "POST",
      body,
      headers: once(key),
    }),
  archiveDebt: (id: string, version: number, archived: boolean) =>
    api<Debt>(`/debts/${enc(id)}/${archived ? "archive" : "unarchive"}`, {
      method: "POST",
      headers: ifMatch(version),
    }),
};
