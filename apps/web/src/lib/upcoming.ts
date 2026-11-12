import type { InstallmentOverview } from "@/lib/installments";
import type { Money } from "@/lib/money";
import type { Occurrence } from "@/lib/recurring";

type Installment = InstallmentOverview["upcoming"][number];

/** One expected payment on Home's timeline. Expected money is never counted as paid. */
export type Stop = {
  key: string;
  date: string;
  name: string;
  /** Null for a variable amount. */
  amount: Money | null;
  status: "overdue" | "due" | "partial" | "expected";
  occurrence: Occurrence | null;
  planId: string | null;
};

const days = (a: string, b: string) =>
  Math.abs(Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`)) / 86_400_000;

const HORIZON_DAYS = 30;

/**
 * An installment is left out when a recurring rule already expects the same payment: one tied to
 * the same debt around that date, or, for a rule set up by hand, the same amount within a few days.
 */
function alreadyExpected(item: Installment, occurrences: Occurrence[]): boolean {
  return occurrences.some((o) => {
    const apart = days(o.window_start, item.due_date);
    if (o.debt?.id === item.debt_id) return apart <= 7;
    return (
      o.expected?.currency === item.amount.currency &&
      Number(o.expected.amount) === Number(item.amount.amount) &&
      apart <= 3
    );
  });
}

function occurrenceStatus(o: Occurrence): Stop["status"] {
  if (o.state === "overdue") return "overdue";
  return o.state === "pending" ? "due" : "expected";
}

function installmentStatus(state: string): Stop["status"] {
  if (state === "overdue" || state === "partial") return state;
  return state === "due" ? "due" : "expected";
}

/** Recurring payments, subscriptions and installments in one list, soonest first, each once. */
export function mergeUpcoming(
  occurrences: Occurrence[],
  installments: Installment[],
  today: string,
): Stop[] {
  const stops: Stop[] = occurrences.map((o) => ({
    key: `o:${o.id}`,
    date: o.window_start,
    name: o.rule_name,
    amount: o.expected,
    status: occurrenceStatus(o),
    occurrence: o,
    planId: null,
  }));
  for (const item of installments) {
    const soon = item.due_date <= today || days(item.due_date, today) <= HORIZON_DAYS;
    if (!soon || alreadyExpected(item, occurrences)) continue;
    stops.push({
      key: `p:${item.plan_id}:${item.sequence}`,
      date: item.due_date,
      name: item.plan_name,
      amount: item.amount,
      status: installmentStatus(item.state),
      occurrence: null,
      planId: item.plan_id,
    });
  }
  return stops.sort((a, b) => a.date.localeCompare(b.date) || a.name.localeCompare(b.name));
}
