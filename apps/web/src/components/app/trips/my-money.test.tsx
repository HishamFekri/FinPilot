import { render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import ar from "../../../../messages/ar.json";
import en from "../../../../messages/en.json";
import { isAppPage } from "@/lib/app-routes";
import type { Transaction } from "@/lib/finance";
import type { Link, Linkable, Position } from "@/lib/links";
import type { Trip } from "@/lib/trips";
import { flow } from "../finance/parts";
import { MyTripMoney } from "./my-money";

vi.mock("../auth-provider", () => ({
  useUser: () => ({ id: "me", time_zone: "UTC" }),
  useAuth: () => ({ handleAuthError: () => false }),
}));
vi.mock("../app-shell", () => ({ usePortalRoot: () => null }));
vi.mock("../finance/finance-provider", () => ({
  useFinance: () => ({ accounts: [], currencies: [{ code: "EUR", exponent: 2 }] }),
}));

const eur = (amount: string) => ({ amount, currency: "EUR" });
const position: Position = {
  trip_id: "t1",
  currency: "EUR",
  paid: eur("90.00"),
  share: eur("30.00"),
  net: eur("60.00"),
  owed_to_me: eur("60.00"),
  i_owe: eur("0.00"),
  settled_sent: eur("0.00"),
  settled_received: eur("0.00"),
  pending_sent: eur("0.00"),
  pending_received: eur("0.00"),
  cash_position: eur("90.00"),
  linked: eur("0.00"),
  unrecorded: eur("90.00"),
  links: 0,
  needs_review: 0,
  shares: [],
};
const linkable: Linkable = {
  trip_id: "t1",
  kind: "expense_payment",
  expense_id: "e1",
  settlement_id: null,
  description: "Dinner",
  date: "2026-11-07",
  amount: eur("90.00"),
  base_amount: eur("90.00"),
};
const reviewed: Link = {
  id: "l1",
  trip_id: "t1",
  trip_name: "Lisbon",
  kind: "expense_payment",
  expense_id: "e2",
  settlement_id: null,
  status: "active",
  linked_base_amount: eur("40.00"),
  transaction: {
    id: "x1",
    type: "trip_expense_payment",
    amount: eur("40.00"),
    account: null,
  } as unknown as Transaction,
  review: { needed: true, reason: "expense_changed", current_base_amount: eur("30.00") },
  created_at: "2026-11-07T10:00:00Z",
  resolved_at: null,
};

const api = vi.hoisted(() => ({
  position: vi.fn(),
  list: vi.fn(),
  linkable: vi.fn(),
  duplicates: vi.fn(),
}));
vi.mock("@/lib/links", async (original) => ({
  ...(await original<typeof import("@/lib/links")>()),
  linksApi: api,
}));

const trip = { id: "t1", base_currency: "EUR" } as Trip;

function show(locale: "en" | "ar") {
  return render(
    <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : ar} timeZone="UTC">
      <MyTripMoney trip={trip} revision={0} onChanged={() => undefined} />
    </NextIntlClientProvider>,
  );
}

describe("your money in a trip", () => {
  it("keeps the amount you paid apart from your share, and says it's private", async () => {
    api.position.mockResolvedValue(position);
    api.list.mockResolvedValue([reviewed]);
    api.linkable.mockResolvedValue([linkable]);
    api.duplicates.mockResolvedValue([]);
    show("en");
    await waitFor(() => expect(screen.getByText("Your money in this trip")).toBeTruthy());
    expect(screen.getByText("Only you see this")).toBeTruthy();
    expect(screen.getByText("Amount you paid")).toBeTruthy();
    expect(screen.getByText("Your share")).toBeTruthy();
    expect(screen.getByText("Owed to you")).toBeTruthy();
    expect(screen.getByText(/money friends pay back is never income/)).toBeTruthy();
    // Nothing is recorded on its own: the person is offered the choice.
    expect(screen.getByRole("button", { name: "Record in my accounts" })).toBeTruthy();
    expect(screen.getByText("An expense you recorded was corrected.")).toBeTruthy();
    expect(screen.getByText(/isn't recorded in your accounts/)).toBeTruthy();
  });

  it("reads in Arabic", async () => {
    api.position.mockResolvedValue(position);
    api.list.mockResolvedValue([]);
    api.linkable.mockResolvedValue([]);
    api.duplicates.mockResolvedValue([]);
    show("ar");
    await waitFor(() => expect(screen.getByText("أموالك في هذه الرحلة")).toBeTruthy());
    expect(screen.getByText("حصتك")).toBeTruthy();
  });
});

describe("trip cash in the person's own lists", () => {
  it("shows a payment into a trip as money out and a reimbursement as money in", () => {
    const txn = (type: Transaction["type"]) => ({ type }) as Transaction;
    expect(flow(txn("trip_expense_payment"))).toBe("out");
    expect(flow(txn("trip_settlement_paid"))).toBe("out");
    expect(flow(txn("trip_settlement_received"))).toBe("in");
  });

  it("has a trip report page", () => {
    expect(isAppPage("/trips/0b8f3c1e-4a7d-4c1b-9c39-2f1e7d1a5b6c/report")).toBe(true);
    expect(isAppPage("/trips/not-an-id/report")).toBe(false);
  });
});
