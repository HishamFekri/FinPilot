import { api } from "@/lib/api";
import type { Money } from "@/lib/money";

/** Trips (API_SPEC.md §9, §10). Every figure, share and balance is calculated by the API. */

export type TripStatus = "active" | "completed" | "cancelled" | "archived";
/** What the trip is now: its stored status, or for an active trip its dates (API: trips.phase). */
export type TripPhase = "upcoming" | "ongoing" | "ended" | "completed" | "cancelled" | "archived";
export type Role = "owner" | "admin" | "member";
// eslint-disable-next-line no-restricted-syntax -- "left" is a membership status, not a CSS class
export type MemberStatus = "active" | "left" | "removed";

export type TripListItem = {
  id: string;
  name: string;
  destination: string | null;
  start_date: string | null;
  end_date: string | null;
  base_currency: string;
  status: TripStatus;
  phase: TripPhase;
  role: Role;
  member_status: MemberStatus;
  members: number;
  waiting_on_me: number;
};

export type Trip = {
  id: string;
  name: string;
  description: string | null;
  destination: string | null;
  start_date: string | null;
  end_date: string | null;
  base_currency: string;
  status: TripStatus;
  phase: TripPhase;
  cancelled_at: string | null;
  /** The group's planned total in the base currency (admins set it). */
  shared_budget: Money | null;
  version: number;
  role: Role;
  member_status: MemberStatus;
  base_currency_locked: boolean;
  can: { edit: boolean; admin: boolean; owner: boolean; add_expense: boolean };
};

export type Person = { user_id: string; display_name: string };
export type Member = Person & { role: Role; status: MemberStatus };

export type SplitMethod = "equal" | "fixed" | "percentage";
export type ExpenseStatus = "approved" | "pending_payer" | "rejected" | "cancelled";

export type Version = {
  id: string;
  version_no: number;
  kind: "initial" | "correction" | "cancellation";
  state: "pending" | "approved" | "rejected" | "withdrawn" | "superseded";
  proposed_by: Person;
  description: string;
  notes: string | null;
  category_key: string;
  expense_date: string;
  amount: Money;
  base_amount: Money;
  rate: {
    rate: string;
    source: string;
    provider: string | null;
    upstream: string | null;
    rate_date: string;
    is_manual: boolean;
  } | null;
  split_method: SplitMethod;
  shares: (Person & { input_value: string | null; share: Money; base_share: Money })[];
  decided_by: Person | null;
  decided_at: string | null;
  decision_reason: string | null;
  created_at: string;
};

export type Expense = {
  id: string;
  status: ExpenseStatus;
  is_corrected: boolean;
  has_pending_correction: boolean;
  classification: "personal" | "paid_for_others" | "shared" | null;
  payer: Person;
  created_by: Person;
  replaces_expense_id: string | null;
  shown: Version;
  current_version: Version | null;
  pending_version: Version | null;
  history: Version[] | null;
  waiting_on_me: boolean;
  can: { correct: boolean; cancel: boolean; approve: boolean; withdraw: boolean; dispute: boolean };
  disputes: { open_count: number; mine: string | null };
  /** Detail only: every dispute on this expense. */
  dispute_history?: Dispute[] | null;
};

export type Dashboard = {
  base_currency: string;
  total: Money;
  shared: Money;
  personal: Money;
  categories: { key: string; amount: Money }[];
  members: (Member & { paid: Money; owed: Money; sent: Money; received: Money; net: Money })[];
  /** Suggested, not paid: nothing counts until a payment is recorded and confirmed. */
  suggestions: {
    payer: Person;
    recipient: Person;
    amount: Money;
    involves_disputed_expense: boolean;
  }[];
  pending_count: number;
  waiting_on_me: {
    expense_id: string;
    version_id: string;
    kind: Version["kind"];
    description: string;
    amount: Money;
    proposed_by: Person;
  }[];
  settlements_waiting: Settlement[];
  pending_settlements: number;
  disputes_attention: Dispute[];
  balanced: boolean;
};

export type Activity = {
  id: string;
  occurred_at: string;
  actor: Person | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  details: Record<string, string>;
};

export type Invitation = {
  id: string;
  label: string | null;
  status: "pending" | "accepted" | "declined" | "revoked";
  expired: boolean;
  expires_at: string;
  created_at: string;
};

export type InvitePreview = {
  trip_name: string;
  destination: string | null;
  start_date: string | null;
  end_date: string | null;
  base_currency: string;
  inviter: string;
  already_member: boolean;
};

export type ExpenseContent = {
  description: string;
  category_key: string;
  expense_date: string;
  amount: Money;
  split: { method: SplitMethod; shares: { user_id: string; value?: string }[] };
  rate?: { rate_id?: string; manual?: string } | null;
  acknowledge_stale_rate?: boolean;
  notes?: string | null;
};

export type ExpenseBody = ExpenseContent & { payer_user_id: string };

export type Preview = {
  amount: Money;
  base_amount: Money;
  rate: string | null;
  classification: "personal" | "paid_for_others" | "shared";
  shares: (Person & { share: Money; base_share: Money })[];
  warnings: string[];
};

/** The shared default expense categories a trip uses (keys, translated by the app). */
export const TRIP_CATEGORIES = [
  "food_and_drinks",
  "groceries",
  "transportation",
  "shopping",
  "housing",
  "bills_utilities",
  "health",
  "education",
  "entertainment",
  "travel",
  "personal_care",
  "subscriptions",
  "gifts",
  "other_expense",
] as const;

const once = (key: string) => ({ "Idempotency-Key": key });
const enc = encodeURIComponent;
const t = (id: string) => `/trips/${enc(id)}`;

export const tripsApi = {
  list: () => api<TripListItem[]>("/trips"),
  create: (
    body: {
      name: string;
      base_currency: string;
      destination?: string | null;
      description?: string | null;
      start_date?: string | null;
      end_date?: string | null;
    },
    key: string,
  ) => api<Trip>("/trips", { method: "POST", body, headers: once(key) }),
  get: (id: string) => api<Trip>(t(id)),
  update: (
    id: string,
    version: number,
    changes: Partial<Omit<Trip, "id" | "shared_budget">> & { shared_budget?: string | null },
  ) =>
    api<Trip>(t(id), { method: "PATCH", body: changes, headers: { "If-Match": `"${version}"` } }),
  status: (
    id: string,
    action: "complete" | "reopen" | "cancel" | "reinstate" | "archive" | "unarchive",
    acknowledge = false,
  ) =>
    api<Trip>(`${t(id)}/${action}`, {
      method: "POST",
      body: action === "archive" ? { acknowledge_outstanding: acknowledge } : undefined,
    }),
  members: (id: string) => api<Member[]>(`${t(id)}/members`),
  setRole: (id: string, userId: string, role: "admin" | "member") =>
    api<Member[]>(`${t(id)}/members/${enc(userId)}`, { method: "PATCH", body: { role } }),
  transfer: (id: string, userId: string) =>
    api<Member[]>(`${t(id)}/transfer-ownership`, { method: "POST", body: { user_id: userId } }),
  remove: (id: string, userId: string) =>
    api<void>(`${t(id)}/members/${enc(userId)}`, { method: "DELETE" }),
  leave: (id: string) => api<void>(`${t(id)}/leave`, { method: "POST" }),
  invitations: (id: string) => api<Invitation[]>(`${t(id)}/invitations`),
  invite: (id: string, label: string | null) =>
    api<Invitation & { token: string }>(`${t(id)}/invitations`, {
      method: "POST",
      body: { label },
    }),
  revoke: (id: string, invitationId: string) =>
    api<void>(`${t(id)}/invitations/${enc(invitationId)}`, { method: "DELETE" }),
  previewInvite: (token: string) =>
    api<InvitePreview>("/invitations/preview", { method: "POST", body: { token } }),
  accept: (token: string) => api<Trip>("/invitations/accept", { method: "POST", body: { token } }),
  decline: (token: string) =>
    api<void>("/invitations/decline", { method: "POST", body: { token } }),
  activity: (id: string) => api<Activity[]>(`${t(id)}/activity`),
  dashboard: (id: string) => api<Dashboard>(`${t(id)}/dashboard`),

  expenses: (id: string) => api<Expense[]>(`${t(id)}/expenses`),
  expense: (id: string, expenseId: string) => api<Expense>(`${t(id)}/expenses/${enc(expenseId)}`),
  preview: (id: string, body: ExpenseBody) =>
    api<Preview>(`${t(id)}/expenses/preview`, { method: "POST", body }),
  record: (id: string, body: ExpenseBody, key: string) =>
    api<Expense>(`${t(id)}/expenses`, { method: "POST", body, headers: once(key) }),
  correct: (id: string, expenseId: string, body: ExpenseContent, key: string) =>
    api<Expense>(`${t(id)}/expenses/${enc(expenseId)}/versions`, {
      method: "POST",
      body,
      headers: once(key),
    }),
  cancel: (id: string, expenseId: string, key: string) =>
    api<Expense>(`${t(id)}/expenses/${enc(expenseId)}/cancel`, {
      method: "POST",
      headers: once(key),
    }),
  decide: (
    id: string,
    versionId: string,
    action: "approve" | "reject" | "withdraw",
    key: string,
    reason?: string | null,
  ) =>
    api<Expense>(`${t(id)}/expense-versions/${enc(versionId)}/${action}`, {
      method: "POST",
      body: action === "reject" ? { reason: reason || null } : undefined,
      headers: once(key),
    }),
};

/** The invite link: the token rides in the fragment, which browsers never send to a server. */
export function inviteLink(origin: string, locale: string, token: string): string {
  return `${origin}/${locale}/invite#${token}`;
}

export const INVITE_STORAGE_KEY = "fp_invite_token";

// --- B4: settlements and disputes

export type SettlementStatus =
  "pending" | "confirmed" | "rejected" | "cancelled" | "reversal_pending" | "reversed";

export type Settlement = {
  id: string;
  kind: "payment" | "reversal";
  state: "pending" | "confirmed" | "rejected" | "cancelled";
  /** What to show: the state, or reversal_pending / reversed for a payment. */
  status: SettlementStatus;
  payer: Person;
  recipient: Person;
  initiated_by: Person;
  /** What was actually paid. */
  amount: Money;
  /** What it counts for in the trip's currency, fixed when recorded. */
  base_amount?: Money;
  /** Paid in another currency: the rate used. */
  rate?: {
    rate: string;
    source: string;
    provider: string | null;
    upstream: string | null;
    rate_date: string;
    is_manual: boolean;
  } | null;
  paid_on: string;
  note: string | null;
  reverses_settlement_id: string | null;
  corrects_settlement_id: string | null;
  reversal_id: string | null;
  decided_by: Person | null;
  decided_at: string | null;
  decision_reason: string | null;
  created_at: string;
  waiting_on_me: boolean;
  can: { confirm: boolean; reject: boolean; cancel: boolean; reverse: boolean };
};

export type Dispute = {
  id: string;
  expense_id: string;
  version_id: string;
  raised_by: Person;
  reason: string;
  status: "open" | "withdrawn" | "upheld" | "dismissed" | "closed_superseded";
  resolution_note: string | null;
  resolved_by: Person | null;
  created_at: string;
  decided_at: string | null;
  can: { withdraw: boolean; resolve: boolean };
};

export const settlementsApi = {
  list: (id: string) => api<Settlement[]>(`${t(id)}/settlements`),
  record: (
    id: string,
    body: {
      recipient_user_id: string;
      amount: Money;
      paid_on?: string | null;
      note?: string | null;
      corrects_settlement_id?: string | null;
      confirm_duplicate?: boolean;
      /** Paid in another currency: a stored rate, a typed rate, or the trip amount settled. */
      rate?: { rate_id?: string; manual?: string } | null;
      base_amount?: string | null;
      acknowledge_stale_rate?: boolean;
    },
    key: string,
  ) =>
    api<Settlement & { warnings: string[] }>(`${t(id)}/settlements`, {
      method: "POST",
      body,
      headers: once(key),
    }),
  act: (
    id: string,
    settlementId: string,
    action: "confirm" | "reject" | "cancel" | "reverse",
    key: string,
    reason?: string | null,
  ) =>
    api<Settlement>(`${t(id)}/settlements/${enc(settlementId)}/${action}`, {
      method: "POST",
      body: action === "reject" ? { reason: reason || null } : undefined,
      headers: once(key),
    }),
};

export const disputesApi = {
  raise: (id: string, expenseId: string, versionId: string, reason: string, key: string) =>
    api<Expense>(`${t(id)}/expenses/${enc(expenseId)}/disputes`, {
      method: "POST",
      body: { version_id: versionId, reason },
      headers: once(key),
    }),
  withdraw: (id: string, disputeId: string) =>
    api<Dispute>(`${t(id)}/disputes/${enc(disputeId)}/withdraw`, { method: "POST" }),
  resolve: (id: string, disputeId: string, outcome: "upheld" | "dismissed", note: string) =>
    api<Dispute>(`${t(id)}/disputes/${enc(disputeId)}/resolve`, {
      method: "POST",
      body: { outcome, note },
    }),
};

// --- Planning, private budget and analytics (FINANCIAL_RULES.md §11.12)

export type PlanKind = "accommodation" | "transport" | "activity" | "note" | "link";
export type TransportMode = "flight" | "train" | "bus" | "car" | "ferry" | "other";
export type BookingStatus = "idea" | "planned" | "booked" | "cancelled";

export type PlanItem = {
  id: string;
  kind: PlanKind;
  title: string;
  transport_mode: TransportMode | null;
  starts_on: string | null;
  ends_on: string | null;
  booking_status: BookingStatus;
  /** Planning only: never spending. */
  estimated: Money | null;
  deposit: Money | null;
  reference: string | null;
  url: string | null;
  notes: string | null;
  expense_id: string | null;
  created_by: string;
  can_edit: boolean;
  version: number;
};

export type PlanItemBody = Partial<{
  kind: PlanKind;
  title: string;
  transport_mode: TransportMode | null;
  starts_on: string | null;
  ends_on: string | null;
  booking_status: BookingStatus;
  estimated: string | null;
  deposit: string | null;
  reference: string | null;
  url: string | null;
  notes: string | null;
  expense_id: string | null;
}>;

export type KeyAmount = { key: string; amount: Money };

export type TripAnalytics = {
  currency: string;
  planned: Money;
  planned_by_kind: KeyAmount[];
  booked: Money;
  deposits: Money;
  actual: Money;
  actual_by_kind: KeyAmount[];
  by_category: KeyAmount[];
  daily: { day: string; amount: Money }[];
  member_count: number;
  cost_per_member: Money;
  members: { user_id: string; display_name: string; paid: Money; share: Money }[];
  shared_budget: Money | null;
  shared_remaining: Money | null;
  shared_percent: string | null;
  my_share: Money;
  my_paid: Money;
  my_by_category: KeyAmount[];
  my_budget: Money | null;
  my_remaining: Money | null;
  my_percent: string | null;
  pending_expenses: number;
  pending_settlements: number;
  confirmed_settlements: Money;
  outstanding: Money;
};

export const planningApi = {
  items: (id: string) => api<PlanItem[]>(`${t(id)}/plan`),
  add: (id: string, body: PlanItemBody) => api<PlanItem>(`${t(id)}/plan`, { method: "POST", body }),
  update: (id: string, itemId: string, version: number, body: PlanItemBody) =>
    api<PlanItem>(`${t(id)}/plan/${enc(itemId)}`, {
      method: "PATCH",
      body,
      headers: { "If-Match": `"${version}"` },
    }),
  remove: (id: string, itemId: string) =>
    api<void>(`${t(id)}/plan/${enc(itemId)}`, { method: "DELETE" }),
  myBudget: (id: string) => api<{ amount: Money | null }>(`${t(id)}/my-budget`),
  setMyBudget: (id: string, amount: string | null) =>
    api<{ amount: Money | null }>(`${t(id)}/my-budget`, { method: "PUT", body: { amount } }),
  analytics: (id: string) => api<TripAnalytics>(`${t(id)}/analytics`),
};
