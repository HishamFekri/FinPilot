import { describe, expect, it } from "vitest";
import type { Occurrence } from "./recurring";
import { mergeUpcoming } from "./upcoming";

const TODAY = "2026-11-09";

const occurrence = (over: Partial<Occurrence>): Occurrence =>
  ({
    id: "o1",
    rule_name: "Rent",
    window_start: "2026-11-12",
    expected: { amount: "500.00", currency: "EUR" },
    state: "expected",
    debt: null,
    ...over,
  }) as Occurrence;

const installment = (over: object = {}) => ({
  plan_id: "p1",
  debt_id: "d1",
  plan_name: "Car",
  sequence: 3,
  due_date: "2026-11-20",
  amount: { amount: "100.00", currency: "EUR" },
  state: "upcoming",
  ...over,
});

describe("upcoming payments", () => {
  it("mixes recurring and installments, soonest first", () => {
    const stops = mergeUpcoming([occurrence({})], [installment()], TODAY);
    expect(stops.map((s) => s.name)).toEqual(["Rent", "Car"]);
    expect(stops[1]!.planId).toBe("p1");
    expect(stops[0]!.occurrence?.id).toBe("o1");
  });

  it("shows an installment once when a rule follows the same debt", () => {
    const rule = occurrence({
      window_start: "2026-11-18",
      debt: { id: "d1", counterparty: "Bank", direction: "i_owe", currency: "EUR" },
    });
    expect(mergeUpcoming([rule], [installment()], TODAY)).toHaveLength(1);
    // A later payment of the same debt is a different one.
    expect(mergeUpcoming([rule], [installment({ due_date: "2026-11-30" })], TODAY)).toHaveLength(2);
  });

  it("shows it once when a hand-made rule has the same amount around the same day", () => {
    const rule = occurrence({
      window_start: "2026-11-21",
      expected: { amount: "100.00", currency: "EUR" },
    });
    expect(mergeUpcoming([rule], [installment()], TODAY)).toHaveLength(1);
    const otherAmount = occurrence({ window_start: "2026-11-21" });
    expect(mergeUpcoming([otherAmount], [installment()], TODAY)).toHaveLength(2);
    const otherCurrency = occurrence({
      window_start: "2026-11-21",
      expected: { amount: "100.00", currency: "USD" },
    });
    expect(mergeUpcoming([otherCurrency], [installment()], TODAY)).toHaveLength(2);
  });

  it("keeps overdue installments however old, and leaves far-off ones out", () => {
    const overdue = installment({ due_date: "2026-09-01", state: "overdue" });
    const far = installment({ due_date: "2027-03-01", sequence: 9 });
    const stops = mergeUpcoming([], [overdue, far], TODAY);
    expect(stops).toHaveLength(1);
    expect(stops[0]!.status).toBe("overdue");
  });

  it("reports each status, and a variable amount as unknown", () => {
    const stops = mergeUpcoming(
      [
        occurrence({ id: "a", state: "overdue", window_start: "2026-11-01" }),
        occurrence({ id: "b", state: "pending", window_start: "2026-11-09", expected: null }),
      ],
      [installment({ state: "partial", due_date: "2026-11-10" })],
      TODAY,
    );
    expect(stops.map((s) => s.status)).toEqual(["overdue", "due", "partial"]);
    expect(stops[1]!.amount).toBeNull();
  });
});
