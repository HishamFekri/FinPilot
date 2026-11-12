import { api } from "@/lib/api";
import type { Money } from "@/lib/money";
import type { Occurrence, Rule } from "@/lib/recurring";

/**
 * Quick templates, savings goals, category groups, the calendar and subscriptions. None of these
 * posts money: a template opens Quick Add, a goal is a number the person tracks, a group only
 * organises categories, and the calendar and subscriptions read what the API already holds.
 */

const once = (key: string) => ({ "Idempotency-Key": key });
const ifMatch = (version: number) => ({ "If-Match": `"${version}"` });
const enc = encodeURIComponent;

function query(params: Record<string, string | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    search.set(k, String(v));
  }
  return search.size ? `?${search}` : "";
}

// --- Quick templates

export type Template = {
  id: string;
  name: string;
  kind: "income" | "expense";
  /** Null: the amount is asked each time. */
  amount: Money | null;
  currency: string;
  /** Null: the default account at the time. */
  account_id: string | null;
  category_id: string;
  description: string | null;
  is_favourite: boolean;
  status: "active" | "archived";
  use_count: number;
  last_used_at: string | null;
  version: number;
};

export type TemplateBody = {
  name?: string;
  kind?: "income" | "expense";
  amount?: Money | null;
  currency?: string;
  account_id?: string | null;
  category_id?: string;
  description?: string | null;
  is_favourite?: boolean;
  from_transaction_id?: string;
};

export const templatesApi = {
  list: (includeArchived = false) =>
    api<Template[]>(`/templates${query({ include_archived: includeArchived })}`),
  create: (body: TemplateBody, key: string) =>
    api<Template>("/templates", { method: "POST", body, headers: once(key) }),
  update: (
    id: string,
    version: number,
    body: Partial<TemplateBody> & {
      clear_amount?: boolean;
      clear_account?: boolean;
      status?: "active" | "archived";
    },
  ) => api<Template>(`/templates/${enc(id)}`, { method: "PATCH", body, headers: ifMatch(version) }),
  remove: (id: string) => api<void>(`/templates/${enc(id)}`, { method: "DELETE" }),
  used: (id: string) => api<Template>(`/templates/${enc(id)}/used`, { method: "POST" }),
};

// --- Savings goals

export type GoalEvent = {
  id: string;
  kind: "created" | "added" | "reduced" | "target_changed" | "status_changed" | "renamed";
  amount: Money | null;
  target: Money | null;
  status: string | null;
  note: string | null;
  created_at: string;
};

export type Goal = {
  id: string;
  name: string;
  target: Money;
  target_date: string | null;
  status: "active" | "paused" | "archived";
  /** Tracked by hand: not money in an account, never added to a balance. */
  progress: Money;
  remaining: Money;
  percent: string;
  reached: boolean;
  version: number;
  history: GoalEvent[] | null;
  /** Set when the goal follows an installment plan: progress comes from payments. */
  plan_id?: string | null;
  plan_name?: string | null;
};

export const goalsApi = {
  list: (includeArchived = false) =>
    api<Goal[]>(`/goals${query({ include_archived: includeArchived })}`),
  get: (id: string) => api<Goal>(`/goals/${enc(id)}`),
  create: (
    body: { name: string; target: Money; target_date?: string | null; starting?: string | null },
    key: string,
  ) => api<Goal>("/goals", { method: "POST", body, headers: once(key) }),
  move: (
    id: string,
    direction: "add" | "reduce",
    amount: string,
    note: string | null,
    key: string,
  ) =>
    api<Goal>(`/goals/${enc(id)}/${direction}`, {
      method: "POST",
      body: { amount, note },
      headers: once(key),
    }),
  update: (
    id: string,
    version: number,
    body: {
      name?: string;
      target?: string;
      target_date?: string | null;
      clear_target_date?: boolean;
      status?: Goal["status"];
    },
  ) => api<Goal>(`/goals/${enc(id)}`, { method: "PATCH", body, headers: ifMatch(version) }),
};

// --- Category groups

export type CategoryGroup = {
  id: string;
  /** Defaults are translated unless renamed. */
  system_key: string | null;
  name: string | null;
  status: "active" | "archived";
  version: number;
};

export type GroupTotal = { group_id: string | null; amount: Money; percent: string };

export const groupsApi = {
  list: (includeArchived = false) =>
    api<CategoryGroup[]>(`/category-groups${query({ include_archived: includeArchived })}`),
  create: (name: string) =>
    api<CategoryGroup>("/category-groups", { method: "POST", body: { name } }),
  update: (
    id: string,
    version: number,
    body: { name?: string; status?: CategoryGroup["status"] },
  ) =>
    api<CategoryGroup>(`/category-groups/${enc(id)}`, {
      method: "PATCH",
      body,
      headers: ifMatch(version),
    }),
  move: (categoryId: string, groupId: string | null) =>
    api<{ id: string; group_id: string | null }>(`/categories/${enc(categoryId)}/group`, {
      method: "PUT",
      body: { group_id: groupId },
    }),
  totals: (params: {
    from?: string;
    to?: string;
    currency?: string;
    trip_allocation?: "include" | "exclude" | "only";
  }) => api<GroupTotal[]>(`/category-groups/totals${query(params)}`),
};

// --- Calendar

export type CalendarDay = {
  day: string;
  income: Money;
  /** Spending, as everywhere: refunds reduce it and trip shares are part of it. */
  expenses: Money;
  transactions: number;
  trip_shares: Money | null;
};

export type CalendarMonth = {
  month: string;
  today: string;
  currency: string | null;
  currencies: string[];
  days: CalendarDay[];
  occurrences: Occurrence[];
  /** Installments expected that month (§4.13): expectations, never spending by themselves. */
  installments?: {
    plan_id: string;
    plan_name: string;
    sequence: number;
    due_date: string;
    amount: Money;
    state: string;
  }[];
};

export const calendarApi = {
  month: (params: { month?: string; currency?: string; account_id?: string }) =>
    api<CalendarMonth>(`/calendar${query(params)}`),
};

// --- Subscriptions (recurring rules marked as such)

export type SubscriptionCost = {
  currency: string;
  monthly: Money;
  annual: Money;
  /** Subscriptions with no fixed amount, left out of the totals. */
  variable: number;
  /** A yearly amount was spread over 12 months (rounded). */
  approximate: boolean;
};

export type Subscriptions = {
  items: Rule[];
  costs: SubscriptionCost[];
  upcoming: Occurrence[];
};

export const subscriptionsApi = {
  get: (days = 30) => api<Subscriptions>(`/subscriptions${query({ days: String(days) })}`),
};
