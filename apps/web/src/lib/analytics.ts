import { api, API_BASE } from "@/lib/api";
import type { Category, Transaction, TransactionType } from "@/lib/finance";
import type { Money } from "@/lib/money";

/** Budgets, Insights and Reports (API_SPEC.md §7, §13). Every figure is calculated by the API. */

export type BudgetState = "normal" | "approaching" | "exceeded";

export type Budget = {
  /** Null: an overall budget for all spending in the currency. */
  category: Category | null;
  currency: string;
  limit: Money;
  spent: Money;
  remaining: Money;
  /** Percent of the limit used, one decimal, as text ("88.9"). */
  percent: string;
  state: BudgetState;
  alert_percent: number;
  effective_from: string;
  /** Spending in the same category in other currencies: shown, never converted or counted. */
  not_counted: Money[];
};

export type ArchivedBudget = {
  category: Category | null;
  currency: string;
  ended_from: string;
  last_limit: Money | null;
};

export type BudgetMonth = { month: string; budgets: Budget[]; archived: ArchivedBudget[] };

export type BudgetDetail = {
  budget: Budget | null;
  history: { from_month: string; limit: Money | null; alert_percent: number }[];
  months: { month: string; limit: Money | null; spent: Money }[];
  transactions: Transaction[];
  /** Same category and month, posted in another currency: shown, never counted. */
  excluded: Transaction[];
  /** Allocated trip shares counted in this budget: what trips cost, not money from an account. */
  trip_shares?: TripShareLine[];
};

export type TripShareLine = {
  trip_id: string;
  trip_name: string;
  expense_id: string;
  description: string;
  expense_date: string;
  category_key: string;
  amount: Money;
};

/** §11.10: whether spending includes trip shares (budgets always do). */
export type AllocationView = "include" | "exclude" | "only";

export type Totals = {
  income: Money;
  /** Spending: everyday spending plus trip shares, per the view. */
  expenses: Money;
  net: Money;
  transactions: number;
  non_trip_expenses?: Money | null;
  trip_shares?: Money | null;
};

export type CategoryShare = {
  category: Category | null;
  amount: Money;
  percent: string;
  previous: Money;
};

export type SeriesPoint = { bucket: string; income: Money; expenses: Money };
export type Grain = "day" | "week" | "month";

export type Insights = {
  currency: string | null;
  currencies: string[];
  date_from: string;
  date_to: string;
  totals: Totals | null;
  previous: Totals | null;
  categories: CategoryShare[];
  monthly: SeriesPoint[];
  daily: SeriesPoint[];
  grain: Grain;
  trip_allocation?: AllocationView;
};

export type BalanceRow = {
  opening: Money;
  initial_balances: Money;
  income: Money;
  expenses: Money;
  transfers_in: Money;
  transfers_out: Money;
  conversions_in: Money;
  conversions_out: Money;
  adjustments: Money;
  debts_in: Money;
  debts_out: Money;
  /** Cash back from trips: never income. */
  trips_in: Money;
  /** Cash paid into trips: never spending (the share is). */
  trips_out: Money;
  /** Money back for purchases: reduces spending, never income. */
  refunds: Money;
  closing: Money;
  reconciles: boolean;
};

export type CurrencyReport = {
  currency: string;
  totals: Totals;
  previous: Totals;
  /** Null when a category or type filter makes balances meaningless (FINANCIAL_RULES.md §16.3). */
  balances: BalanceRow | null;
  categories: CategoryShare[];
  grain: Grain;
  series: SeriesPoint[];
  /** The trip shares inside `totals.expenses`, by trip. */
  trips?: { trip_id: string; trip_name: string; amount: Money; count: number }[];
};

export type ReportOccurrence = {
  name: string;
  kind: string;
  window_start: string;
  window_end: string;
  state: "expected" | "pending" | "overdue" | "confirmed" | "skipped";
  expected: Money | null;
  actual: Money | null;
};

export type ReportDebt = {
  id: string;
  counterparty: string;
  direction: "i_owe" | "owed_to_me";
  original: Money;
  repaid_in_period: Money;
  remaining: Money;
};

export type ReportFilters = {
  from?: string;
  to?: string;
  all_history?: boolean;
  type?: TransactionType;
  account_id?: string;
  category_id?: string;
  currency?: string;
  q?: string;
  trip_allocation?: AllocationView;
};

export type Report = {
  generated_at: string;
  time_zone: string;
  filters: {
    date_from: string;
    date_to: string;
    type: string | null;
    account_id: string | null;
    category_id: string | null;
    currency: string | null;
    q: string | null;
  };
  balances_applicable: boolean;
  by_currency: CurrencyReport[];
  /** Every posted transaction in the period and filters (all types). */
  transaction_count: number;
  /** The transaction table's rows: the same, narrowed by the description search. */
  table_count: number;
  /** Rows a PDF (and Print) or a CSV can hold. */
  limits: { pdf_rows: number; csv_rows: number } | null;
  recurring: ReportOccurrence[];
  debts: ReportDebt[];
};

function query(params: Record<string, string | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(name, String(value));
  }
  return search.size ? `?${search}` : "";
}

// --- Spending Intelligence (FINANCIAL_RULES.md §16.6)

export type IntelligencePeriod = "mtd" | "qtd" | "half" | "ytd" | "rolling6" | "last12" | "custom";
type Span = { from: string; to: string; days: number };

export type ComparisonKey =
  | "mtd"
  | "complete_month"
  | "qtd_previous_quarter"
  | "qtd_previous_year"
  | "ytd"
  | "half"
  | "rolling6";

export type Comparison = {
  key: ComparisonKey;
  current: Span;
  previous: Span;
  current_amount: Money;
  previous_amount: Money;
  difference: Money;
  /** Null when there's no honest base; `reason` says why. */
  percent: string | null;
  reason: "no_history" | "partial_history" | "zero_previous" | null;
};

export type IntelligenceMonth = {
  month: string;
  partial: boolean;
  income: Money;
  spent: Money;
  scheduled: Money;
  variable: Money;
  financed: Money;
  trip: Money;
};

export type IntelligenceHighlight = {
  category: Category | null;
  current: Money;
  previous: Money;
  change: Money;
  percent: string | null;
};

export type IntelligenceHeatmap = {
  months: string[];
  rows: { category: Category | null; values: Money[] }[];
};

export type IntelligenceBudget = {
  category: Category | null;
  limit: Money;
  spent: Money;
  percent: string;
  state: BudgetState;
  alert_percent: number;
};

export type IntelligenceInsight = {
  code:
    | "spending_up"
    | "spending_down"
    | "driver_up"
    | "driver_down"
    | "budget_near"
    | "budget_over"
    | "subscriptions_share"
    | "pace_up"
    | "pace_down"
    | "commitments_high";
  kind: "comparison" | "category" | "budget" | "share" | "pace" | "commitments";
  category?: Category | null;
  current?: Money | null;
  previous?: Money | null;
  difference?: Money | null;
  limit?: Money | null;
  total?: Money | null;
  percent?: string | null;
  months?: number;
  days?: number;
};

export type Intelligence = {
  as_of: string;
  period: IntelligencePeriod;
  window: Span;
  previous: Span;
  first_activity: string | null;
  currency: string | null;
  currencies: string[];
  notes: (
    | "account_excludes_trip_shares"
    | "category_hides_income_and_commitments"
    | "income_only"
    | "refunds_excluded"
    | "trip_exclude"
    | "trip_only"
    | "budgets_ignore_filters"
  )[];
  trend_end: string;
  empty: boolean;
  totals?: { income: Money; spent: Money; net: Money };
  comparisons?: Comparison[];
  selected?: Comparison;
  pace?: {
    so_far: Money;
    days_elapsed: number;
    days_in_month: number;
    average_daily: Money;
    historical_daily: Money | null;
    difference_daily: Money | null;
    percent: string | null;
    months_used: number;
    enough_history: boolean;
    projected: Money | null;
    projection_reason: "too_early" | "month_over" | null;
  };
  months?: IntelligenceMonth[];
  categories?: CategoryShare[];
  highlights?: {
    highest: IntelligenceHighlight | null;
    fastest: IntelligenceHighlight | null;
    largest_increase: IntelligenceHighlight | null;
    largest_decrease: IntelligenceHighlight | null;
    new: IntelligenceHighlight[];
    unusual: (IntelligenceHighlight & {
      typical: Money;
      months: number;
    })[];
  };
  heatmap?: IntelligenceHeatmap;
  weekdays?: { weekday: number; days: number; total: Money; average: Money }[];
  daily?: { day: string; amount: Money }[] | null;
  fixed?: {
    scheduled: Money;
    subscriptions: Money;
    variable: Money;
    financed: Money;
    trip: Money;
  };
  budgets?: IntelligenceBudget[];
  commitments?: {
    income: Money;
    spent: Money;
    net: Money;
    confirmed: Money;
    upcoming_recurring: Money;
    upcoming_installments: Money;
    upcoming_total: Money;
    upcoming_unknown: number;
    upcoming_days: number;
    installment_monthly: Money;
    installment_income_ratio: string | null;
    income_months: number;
    remaining_after_known: Money;
  } | null;
  trips?: {
    everyday: Money;
    shares: Money;
    total: Money;
    percent: string | null;
    cash_paid: Money | null;
    cash_received: Money | null;
    by_trip: { id: string; name: string; amount: Money }[];
  } | null;
  insights?: IntelligenceInsight[];
  equivalent?: {
    target: string;
    method: string;
    current: Money | null;
    previous: Money | null;
    percent: string | null;
    complete: boolean;
    is_stale: boolean;
    parts: {
      currency: string;
      spent: Money;
      status: "same" | "ok" | "unavailable";
      equivalent: Money | null;
      rate: string | null;
      provider: string | null;
      upstream: string | null;
      rate_date: string | null;
    }[];
  } | null;
};

export const intelligenceApi = {
  get: (params: {
    period: IntelligencePeriod;
    as_of?: string;
    from?: string;
    to?: string;
    currency?: string;
    account_id?: string;
    category_id?: string;
    type?: string;
    trip_allocation?: string;
    equivalent?: string;
    trend_end?: string;
  }) => api<Intelligence>(`/analytics/intelligence${query(params)}`),
};

export const budgetsApi = {
  month: (month?: string) => api<BudgetMonth>(`/budgets${query({ month })}`),
  set: (body: {
    category_id: string | null;
    limit: Money;
    from_month: string;
    alert_percent: number;
  }) => api<Budget>("/budgets", { method: "PUT", body }),
  end: (body: { category_id: string | null; currency: string; from_month: string }) =>
    api<unknown>("/budgets/end", { method: "POST", body }),
  /** Brings an ended budget back from a month on, with its last limit. */
  restore: (body: { category_id: string | null; currency: string; from_month: string }) =>
    api<BudgetMonth>("/budgets/restore", { method: "POST", body }),
  /** Only for a budget no earlier month uses (409 budget_has_history otherwise). */
  remove: (body: { category_id: string | null; currency: string }) =>
    api<BudgetMonth>("/budgets/delete", { method: "POST", body }),
  detail: (params: { category_id: string | null; currency: string; month: string }) =>
    api<BudgetDetail>(`/budgets/detail${query(params)}`),
};

export const insightsApi = {
  get: (params: {
    from?: string;
    to?: string;
    all_history?: boolean;
    currency?: string;
    account_id?: string;
    trip_allocation?: AllocationView;
  }) => api<Insights>(`/insights${query(params)}`),
};

export const reportsApi = {
  get: (filters: ReportFilters) => api<Report>(`/reports${query(filters)}`),
  /** Plain links: the browser downloads the file with the session cookie. */
  csvUrl: (filters: ReportFilters, locale: string) =>
    `${API_BASE}/reports/export.csv${query({ ...filters, locale })}`,
  pdfUrl: (filters: ReportFilters, locale: string) =>
    `${API_BASE}/reports/export.pdf${query({ ...filters, locale })}`,
  tripSharesCsvUrl: (filters: ReportFilters, locale: string) =>
    `${API_BASE}/reports/trip-shares.csv${query({ ...filters, locale })}`,
};

// --- Periods. Dates are ISO days in the user's time zone; all arithmetic is on calendar days.

export type PeriodKey = "today" | "week" | "month" | "lastMonth" | "custom" | "all";

function toIso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function at(iso: string): Date {
  return new Date(`${iso}T12:00:00Z`);
}

export function addDays(iso: string, days: number): string {
  const d = at(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return toIso(d);
}

export function monthStart(iso: string): string {
  return `${iso.slice(0, 7)}-01`;
}

export function addMonths(month: string, months: number): string {
  const [year, m] = month.split("-").map(Number) as [number, number];
  const index = year * 12 + (m - 1) + months;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

export function monthEnd(iso: string): string {
  return addDays(`${addMonths(iso.slice(0, 7), 1)}-01`, -1);
}

/**
 * The dates for a preset. Weeks start on Monday (ISO 8601), the same in every language, so a
 * report reads the same for everyone. "All" leaves the start to the API (the first transaction).
 */
export function periodRange(
  key: Exclude<PeriodKey, "custom">,
  today: string,
): { from?: string; to: string; all_history?: boolean } {
  switch (key) {
    case "today":
      return { from: today, to: today };
    case "week": {
      const weekday = (at(today).getUTCDay() + 6) % 7; // Monday = 0
      return { from: addDays(today, -weekday), to: today };
    }
    case "month":
      return { from: monthStart(today), to: today };
    case "lastMonth": {
      const first = `${addMonths(today.slice(0, 7), -1)}-01`;
      return { from: first, to: monthEnd(first) };
    }
    case "all":
      return { to: today, all_history: true };
  }
}

/** A drawing ratio for bars (0..1). Never a displayed or stored amount. */
export function ratio(part: string, whole: string): number {
  const p = Number(part);
  const w = Number(whole);
  if (!Number.isFinite(p) || !Number.isFinite(w) || w <= 0) return 0;
  return Math.min(1, Math.max(0, p / w));
}
