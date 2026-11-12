import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../../../messages/en.json";
import { type Report, reportsApi } from "@/lib/analytics";
import { financeApi, type Transaction } from "@/lib/finance";
import { ReportsView } from "./reports-view";

vi.mock("../auth-provider", () => ({
  useUser: () => ({ time_zone: "UTC" }),
  useAuth: () => ({ handleAuthError: () => false }),
}));
vi.mock("../finance/finance-provider", () => ({
  FinanceGate: ({ children }: { children: React.ReactNode }) => children,
  useFinance: () => ({ revision: 0, categories: [], accounts: [], currencies: [] }),
}));
vi.mock("../finance/transaction-dialog", () => ({
  useTxnDialog: () => ({ show: vi.fn() }),
  TransactionDialog: () => null,
}));

const money = (amount: string) => ({ amount, currency: "TRY" });
const totals = {
  income: money("0.00"),
  expenses: money("60.00"),
  net: money("-60.00"),
  transactions: 3,
};

function report(tableCount: number, pdfRows: number): Report {
  return {
    generated_at: "2026-11-07T10:00:00Z",
    time_zone: "UTC",
    filters: {
      date_from: "2026-11-01",
      date_to: "2026-11-07",
      type: null,
      account_id: null,
      category_id: null,
      currency: null,
      q: null,
    },
    balances_applicable: true,
    by_currency: [
      {
        currency: "TRY",
        totals,
        previous: totals,
        balances: null,
        categories: [],
        grain: "day",
        series: [],
      },
    ],
    transaction_count: tableCount,
    table_count: tableCount,
    limits: { pdf_rows: pdfRows, csv_rows: 50000 },
    recurring: [],
    debts: [],
  };
}

const TXN: Transaction = {
  id: "t1",
  type: "expense",
  status: "posted",
  effective_date: "2026-11-07",
  amount: money("10.00"),
  direction: null,
  account: { id: "a", name: "Bank", system_key: null, currency: "TRY", status: "active" },
  counter_account: null,
  counter_amount: null,
  category: null,
  description: "Groceries",
  notes: null,
  created_at: "2026-11-07T10:00:00Z",
  updated_at: "2026-11-07T10:00:00Z",
  version: 1,
};

function show() {
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ReportsView />
    </NextIntlClientProvider>,
  );
}

let opened: { document: Document; location: { href: string }; close: () => void } | null;

beforeEach(() => {
  opened = null;
  vi.spyOn(financeApi, "history").mockResolvedValue({ items: [TXN], next_cursor: "more" });
  vi.spyOn(window, "open").mockImplementation(() => {
    const doc = document.implementation.createHTMLDocument("print");
    opened = { document: doc, location: { href: "" }, close: vi.fn() };
    return opened as unknown as Window;
  });
  URL.createObjectURL = vi.fn(() => "blob:report");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("printing a report", () => {
  it("warns on the browser's own printout when rows aren't loaded", async () => {
    vi.spyOn(reportsApi, "get").mockResolvedValue(report(3, 2000));
    show();
    expect(
      await screen.findByText(
        "Printed from the browser's menu: lists only the 1 of 3 transactions loaded on screen. Use the Print button for the complete report.",
      ),
    ).toBeTruthy();
  });

  it("prints the complete report from the API, not the loaded rows", async () => {
    vi.spyOn(reportsApi, "get").mockResolvedValue(report(3, 2000));
    const fetchMock = vi.fn(
      async () =>
        new Response(new Blob(["%PDF"]), {
          status: 200,
          headers: {
            "X-Report-Complete": "true",
            "X-Report-Rows-Included": "3",
            "X-Report-Rows-Total": "3",
          },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    show();
    await screen.findByText("Groceries");
    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    await waitFor(() => expect(opened?.location.href).toBe("blob:report"));
    expect(String((fetchMock.mock.calls as unknown[][])[0]?.[0])).toContain(
      "/api/v1/reports/export.pdf?",
    );
    expect(await screen.findByText("PDF ready: it lists all 3 transactions.")).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("explains the limit first and labels the result partial", async () => {
    vi.spyOn(reportsApi, "get").mockResolvedValue(report(3, 2));
    const fetchMock = vi.fn(
      async () =>
        new Response(new Blob(["%PDF"]), {
          status: 200,
          headers: {
            "X-Report-Complete": "false",
            "X-Report-Rows-Included": "2",
            "X-Report-Rows-Total": "3",
          },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    show();
    await screen.findByText("Groceries");
    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    expect(screen.getByText("This report is longer than one PDF can hold")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Print partial report" }));
    expect(
      await screen.findByText(
        "PDF ready, marked PARTIAL: it lists 2 of 3 transactions. Export CSV for every row.",
      ),
    ).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("says so when the print window is blocked", async () => {
    vi.spyOn(reportsApi, "get").mockResolvedValue(report(1, 2000));
    vi.spyOn(window, "open").mockReturnValue(null);
    show();
    await screen.findByText("Groceries");
    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    expect(await screen.findByText(/blocked the print window/)).toBeTruthy();
  });
});
