import { api } from "@/lib/api";
import type { Transaction } from "@/lib/finance";
import type { Money } from "@/lib/money";
import type { Expense, ExpenseBody, Person } from "@/lib/trips";

/**
 * Personal links (FINANCIAL_RULES §11): the person's own bridge from a trip to their accounts.
 * Owner-only: nothing here is ever part of a trip response. A link records cash that really
 * moved; what the trip cost them is their share, which the API counts in spending by itself.
 */

export type LinkKind = "expense_payment" | "settlement_paid" | "settlement_received";

export type Link = {
  id: string;
  trip_id: string;
  trip_name: string;
  kind: LinkKind;
  expense_id: string | null;
  settlement_id: string | null;
  status: "active" | "unlinked";
  /** Trip currency, signed: + paid into the trip, - received back. */
  linked_base_amount: Money;
  transaction: Transaction;
  review: {
    needed: boolean;
    reason: "expense_changed" | "expense_cancelled" | "settlement_reversed" | null;
    current_base_amount: Money | null;
  };
  created_at: string;
  resolved_at: string | null;
};

/** Something the person could record: an approved expense they paid, a confirmed payment. */
export type Linkable = {
  trip_id: string;
  kind: LinkKind;
  expense_id: string | null;
  settlement_id: string | null;
  description: string | null;
  date: string;
  amount: Money;
  base_amount: Money;
};

export type Position = {
  trip_id: string;
  currency: string;
  paid: Money;
  share: Money;
  net: Money;
  owed_to_me: Money;
  i_owe: Money;
  settled_sent: Money;
  settled_received: Money;
  pending_sent: Money;
  pending_received: Money;
  cash_position: Money;
  linked: Money;
  unrecorded: Money;
  links: number;
  needs_review: number;
  shares: {
    expense_id: string;
    description: string;
    expense_date: string;
    category_key: string;
    amount: Money;
  }[];
};

export type DuplicateGroup = {
  trip_id: string;
  trip_name: string;
  expense_id: string;
  description: string;
  expense_date: string;
  amount: Money;
  base_amount: Money;
  my_share: Money;
  paid_by_me: boolean;
  candidates: {
    transaction: Transaction;
    strength: "strong" | "possible";
    match: "original" | "base" | "share" | "near_base";
  }[];
};

export type LinkBody = {
  trip_id: string;
  expense_id?: string | null;
  settlement_id?: string | null;
  mode?: "create" | "attach";
  account_id?: string;
  amount?: string;
  effective_date?: string | null;
  transaction_id?: string;
  transaction_version?: number;
  confirm?: boolean;
};

const once = (key: string) => ({ "Idempotency-Key": key });
const enc = encodeURIComponent;

export const linksApi = {
  list: (tripId?: string) => api<Link[]>(`/links${tripId ? `?trip_id=${enc(tripId)}` : ""}`),
  linkable: (tripId: string) => api<Linkable[]>(`/links/suggestions?trip_id=${enc(tripId)}`),
  create: (body: LinkBody, key: string) =>
    api<Link>("/links", { method: "POST", body, headers: once(key) }),
  resolve: (
    id: string,
    body: { action: "update" | "keep" | "reverse"; amount?: string; effective_date?: string },
    key: string,
  ) => api<Link>(`/links/${enc(id)}/resolve`, { method: "POST", body, headers: once(key) }),
  unlink: (id: string, personalTransaction: "reverse" | "convert") =>
    api<Link>(`/links/${enc(id)}?personal_transaction=${personalTransaction}`, {
      method: "DELETE",
    }),
  position: (tripId: string) => api<Position>(`/me/trips/${enc(tripId)}/position`),
  /** The payer records a trip expense and their own cash for it, in one step. */
  expenseWithLink: (
    tripId: string,
    expense: ExpenseBody,
    link: { account_id: string; amount: string; effective_date?: string | null },
    key: string,
  ) =>
    api<{ expense: Expense; link: Link }>("/links/expense", {
      method: "POST",
      body: { trip_id: tripId, expense, link },
      headers: once(key),
    }),
  /** The payer approves an expense someone else recorded and records their cash at once. */
  approveWithLink: (
    tripId: string,
    versionId: string,
    link: { account_id: string; amount: string; effective_date?: string | null },
    key: string,
  ) =>
    api<{ expense: Expense; link: Link }>("/links/approve", {
      method: "POST",
      body: { trip_id: tripId, version_id: versionId, link },
      headers: once(key),
    }),
  duplicates: (params: { trip_id?: string; expense_id?: string } = {}) => {
    const query = new URLSearchParams(params as Record<string, string>);
    return api<DuplicateGroup[]>(`/me/possible-duplicates${query.size ? `?${query}` : ""}`);
  },
  dismiss: (transactionId: string, expenseId: string) =>
    api<void>("/me/possible-duplicates/dismiss", {
      method: "POST",
      body: { transaction_id: transactionId, expense_id: expenseId },
    }),
};

// --- The trip report (trip figures only)

export type TripReportFilters = {
  from?: string;
  to?: string;
  category?: string;
  member_id?: string;
};

export type ReportPayment = {
  id: string;
  kind: "payment" | "reversal";
  status: string;
  payer: Person;
  recipient: Person;
  amount: Money;
  paid_on: string;
};

export type TripReport = {
  trip_id: string;
  name: string;
  destination: string | null;
  start_date: string | null;
  end_date: string | null;
  base_currency: string;
  status: string;
  generated_at: string;
  scope: "full" | "mine";
  filters: {
    date_from: string | null;
    date_to: string | null;
    category_key: string | null;
    member_id: string | null;
  };
  filtered: boolean;
  total: Money;
  expense_count: number;
  pending_expenses: number;
  by_category: { category_key: string; amount: Money; percent: string }[];
  members: (Person & {
    role: string;
    status: string;
    paid: Money;
    benefited: Money;
    trip_paid: Money;
    trip_share: Money;
    sent: Money;
    received: Money;
    pending_sent: Money;
    pending_received: Money;
    net: Money;
  })[];
  outstanding: Money;
  balanced: boolean;
  settlements_confirmed: ReportPayment[];
  settlements_pending: ReportPayment[];
  suggestions: { payer: Person; recipient: Person; amount: Money }[];
  expenses: {
    id: string;
    expense_date: string;
    description: string;
    category_key: string;
    payer: Person;
    amount: Money;
    base_amount: Money;
    rate: string | null;
    rate_source: string | null;
    shares: { person: Person; amount: Money }[];
  }[];
  limits: { pdf_rows: number; csv_rows: number };
};

function reportQuery(filters: TripReportFilters, extra: Record<string, string> = {}): string {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...filters, ...extra })) if (v) query.set(k, v);
  return query.size ? `?${query}` : "";
}

export const tripReportApi = {
  summary: (tripId: string, filters: TripReportFilters) =>
    api<TripReport>(`/trips/${enc(tripId)}/reports/summary${reportQuery(filters)}`),
  /** A same-origin download link (the browser sends the session cookie). */
  fileUrl: (
    tripId: string,
    file: "expenses.csv" | "balances.csv" | "settlements.csv" | "export.pdf",
    filters: TripReportFilters,
    locale: string,
  ) => `/api/v1/trips/${enc(tripId)}/reports/${file}${reportQuery(filters, { locale })}`,
};
