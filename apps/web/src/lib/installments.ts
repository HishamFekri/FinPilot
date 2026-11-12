import { api } from "@/lib/api";
import type { Money } from "@/lib/money";

/**
 * Installment plans (FINANCIAL_RULES.md §4.13, API_SPEC.md §11). Every figure comes from the API:
 * what remains is the debt's ledger balance, and which installments are paid is worked out from
 * confirmed payments only. The schedule is an expectation and never moves money.
 */

const once = (key: string) => ({ "Idempotency-Key": key });
const ifMatch = (version: number) => ({ "If-Match": `"${version}"` });
const enc = encodeURIComponent;

export type InstallmentState = "paid" | "partial" | "due" | "overdue" | "upcoming" | "superseded";

export type InstallmentItem = {
  id: string;
  sequence: number;
  due_date: string;
  principal: Money;
  interest: Money;
  fee: Money;
  total: Money;
  covered: Money;
  state: InstallmentState;
};

export type InstallmentPayment = {
  id: string;
  paid_on: string;
  principal: Money | null;
  interest: Money | null;
  fee: Money | null;
  voided: boolean;
  principal_transaction_id: string | null;
  interest_transaction_id: string | null;
  fee_transaction_id: string | null;
};

export type InstallmentPlan = {
  id: string;
  name: string;
  lender: string | null;
  currency: string;
  status: "active" | "archived";
  origin: "purchase" | "loan" | "existing";
  item_price: Money | null;
  down_payment: Money | null;
  principal: Money;
  paid: Money;
  remaining: Money;
  installment_count: number;
  paid_count: number;
  paid_before_count: number;
  remaining_count: number;
  interest_mode: "none" | "fixed";
  interest_paid: Money;
  fees_paid: Money;
  scheduled_interest_left: Money;
  next_due: InstallmentItem | null;
  overdue_count: number;
  completion_date: string | null;
  ahead: Money;
  behind: Money;
  settled: boolean;
  goal_id: string | null;
  goal_name: string | null;
  purchase_transaction_id: string | null;
  notes: string | null;
  version: number;
  items?: InstallmentItem[] | null;
  superseded?: InstallmentItem[] | null;
  payments?: InstallmentPayment[] | null;
};

export type PlanBody = {
  origin: "purchase" | "loan" | "existing";
  name: string;
  lender?: string | null;
  currency: string;
  installment_count: number;
  first_due_date: string;
  item_price?: string | null;
  down_payment?: string | null;
  category_id?: string | null;
  account_id?: string | null;
  start_date?: string | null;
  principal?: string | null;
  paid_before_count?: number;
  interest_per_installment?: string | null;
  fee_per_installment?: string | null;
  notes?: string | null;
  goal_id?: string | null;
};

export type PlanPreview = {
  count: number;
  principal: Money;
  per_installment: Money[];
  due_dates: string[];
  interest_per_installment: Money;
  fee_per_installment: Money;
  total_to_pay: Money;
  completion_date: string;
};

export type PaymentBody = {
  account_id: string;
  principal?: string | null;
  principal_account_amount?: string | null;
  interest?: string | null;
  fee?: string | null;
  paid_on?: string | null;
  note?: string | null;
};

export type CurrencyOverview = {
  currency: string;
  monthly: Money;
  remaining_principal: Money;
  scheduled_interest: Money;
  remaining_obligations: Money;
  remaining_count: number;
  overdue_count: number;
  overdue: Money;
  due_within: { months: number; amount: Money }[];
  average_income: Money | null;
  income_months: number;
  income_ratio_percent: string | null;
  last_payoff: string | null;
};

export type InstallmentOverview = {
  currencies: CurrencyOverview[];
  upcoming: {
    plan_id: string;
    debt_id: string;
    plan_name: string;
    sequence: number;
    due_date: string;
    amount: Money;
    state: string;
  }[];
  plan_count: number;
};

const p = (id: string) => `/installments/${enc(id)}`;

export const installmentsApi = {
  list: (includeArchived = false) =>
    api<InstallmentPlan[]>(`/installments${includeArchived ? "?include_archived=true" : ""}`),
  get: (id: string) => api<InstallmentPlan>(p(id)),
  preview: (body: PlanBody) => api<PlanPreview>("/installments/preview", { method: "POST", body }),
  create: (body: PlanBody, key: string) =>
    api<InstallmentPlan>("/installments", { method: "POST", body, headers: once(key) }),
  pay: (id: string, body: PaymentBody, key: string) =>
    api<InstallmentPlan>(`${p(id)}/payments`, { method: "POST", body, headers: once(key) }),
  reschedule: (
    id: string,
    version: number,
    body: {
      remaining_count: number;
      first_due_date: string;
      interest_per_installment?: string | null;
      fee_per_installment?: string | null;
    },
  ) =>
    api<InstallmentPlan>(`${p(id)}/reschedule`, {
      method: "POST",
      body,
      headers: ifMatch(version),
    }),
  update: (
    id: string,
    version: number,
    body: { name?: string; lender?: string | null; notes?: string | null; goal_id?: string | null },
  ) => api<InstallmentPlan>(p(id), { method: "PATCH", body, headers: ifMatch(version) }),
  action: (id: string, version: number, action: "archive" | "restore" | "remove") =>
    api<InstallmentPlan>(`${p(id)}/${action}`, { method: "POST", headers: ifMatch(version) }),
  overview: () => api<InstallmentOverview>("/installments-overview"),
};

/**
 * What-if (display only): the share of a monthly income that installments take, to one decimal.
 * Null without a positive income. Never advice, never a credit decision.
 */
export function ratioPercent(monthly: number, income: number): number | null {
  if (!(income > 0) || !(monthly >= 0)) return null;
  return Math.round((monthly / income) * 1000) / 10;
}
