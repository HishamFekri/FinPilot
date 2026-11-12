// Illustrative data for the landing page only. Nothing here comes from, or reaches, the API.

export type CategoryKey =
  "rent" | "groceries" | "eating" | "transport" | "fun" | "health" | "other";

/** October's story, in the order it's told. Amounts in EUR. */
export const STORY_START_BALANCE = 640;
export const STORY_EVENTS = [
  { key: "salary", day: 1, amount: 2900 },
  { key: "rent", day: 2, amount: -950 },
  { key: "groceries", day: 9, amount: -412.8 },
  { key: "transport", day: 16, amount: -96.5 },
  { key: "fun", day: 24, amount: -64 },
] as const;

/** Trip Mode: three friends in Cappadocia. Shares are equal; nets sum to zero. */
export const TRIP_PEOPLE = [
  { id: "deniz", name: "Deniz", tone: "tang" },
  { id: "lina", name: "Lina", tone: "sky" },
  { id: "sami", name: "Sami", tone: "ink" },
] as const;
export type PersonId = (typeof TRIP_PEOPLE)[number]["id"];

export const TRIP_EXPENSES: {
  key: "hotel" | "balloon" | "dinner";
  amount: number;
  paidBy: PersonId;
}[] = [
  { key: "hotel", amount: 360, paidBy: "sami" },
  { key: "balloon", amount: 450, paidBy: "deniz" },
  { key: "dinner", amount: 90, paidBy: "lina" },
];

export function tripNets(): Record<PersonId, number> {
  const share = TRIP_EXPENSES.reduce((sum, e) => sum + e.amount, 0) / TRIP_PEOPLE.length;
  const nets = Object.fromEntries(TRIP_PEOPLE.map((p) => [p.id, -share])) as Record<
    PersonId,
    number
  >;
  for (const e of TRIP_EXPENSES) nets[e.paidBy] += e.amount;
  return nets;
}

export const TRIP_SETTLEMENTS: { from: PersonId; to: PersonId; amount: number }[] = [
  { from: "lina", to: "deniz", amount: 150 },
  { from: "lina", to: "sami", amount: 60 },
];
