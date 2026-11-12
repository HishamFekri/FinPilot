import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ar from "../../../../messages/ar.json";
import en from "../../../../messages/en.json";
import type { Account } from "@/lib/accounts";
import type { Me } from "@/lib/auth";
import { type Occurrence, recurringApi } from "@/lib/recurring";
import { ConfirmDialog, OccurrenceRow } from "./parts";

const USER: Me = {
  id: "u1",
  email: "zeynep@example.com",
  display_name: "Zeynep Kaya",
  avatar_url: null,
  locale: "en",
  preferred_currency: "TRY",
  time_zone: "UTC",
  status: "active",
  created_at: "2026-11-05T09:30:00Z",
};

const BANK: Account = {
  id: "bank",
  name: null,
  system_key: "bank",
  is_default: true,
  description: null,
  type: "bank",
  currency: "TRY",
  balance: { amount: "0.00", currency: "TRY" },
  status: "active",
  opened_on: "2026-11-01",
  version: 1,
};

const changed = vi.fn(async () => {});
vi.mock("../auth-provider", () => ({
  useUser: () => USER,
  useAuth: () => ({ handleAuthError: () => false }),
}));
vi.mock("../finance/finance-provider", () => ({
  useFinance: () => ({
    accounts: [BANK],
    categories: [],
    currencies: [{ code: "TRY", exponent: 2 }],
    defaultAccountId: "bank",
    changed,
  }),
}));
vi.mock("../app-shell", () => ({ usePortalRoot: () => ({ current: document.body }) }));

function occurrence(over: Partial<Occurrence> = {}): Occurrence {
  return {
    id: "o1",
    rule_id: "r1",
    rule_name: "Salary",
    kind: "income",
    sequence: 1,
    total: null,
    window_start: "2026-11-25",
    window_end: "2026-11-28",
    expected: { amount: "35000.00", currency: "TRY" },
    actual: null,
    account: { id: "bank", name: null, system_key: "bank", currency: "TRY", status: "active" },
    category: null,
    debt: null,
    status: "pending",
    state: "pending",
    remind: true,
    transaction_id: null,
    version: 1,
    ...over,
  };
}

function withIntl(node: ReactNode, locale: "en" | "ar" = "en") {
  return render(
    <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : ar} timeZone="UTC">
      {node}
    </NextIntlClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("an expected payment", () => {
  it("says what's expected, when, and offers Received, Not yet and Skip", () => {
    const onConfirm = vi.fn();
    withIntl(
      <ul>
        <OccurrenceRow occurrence={occurrence()} onConfirm={onConfirm} onAnswered={vi.fn()} />
      </ul>,
    );
    expect(screen.getByText("Salary")).toBeInTheDocument();
    expect(screen.getByText("Nov 25 – 28")).toBeInTheDocument();
    expect(screen.getByText("₺35,000.00")).toBeInTheDocument();
    expect(screen.getByText("Due")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Received" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Not yet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });

  it("an expense asks whether it was paid, and an answered one has no buttons", () => {
    withIntl(
      <ul>
        <OccurrenceRow
          occurrence={occurrence({ kind: "expense", rule_name: "Rent" })}
          onConfirm={vi.fn()}
          onAnswered={vi.fn()}
        />
        <OccurrenceRow
          occurrence={occurrence({
            id: "o2",
            status: "confirmed",
            state: "confirmed",
            actual: { amount: "34500.00", currency: "TRY" },
          })}
          onConfirm={vi.fn()}
          onAnswered={vi.fn()}
        />
      </ul>,
    );
    expect(screen.getAllByRole("button", { name: "Paid" })).toHaveLength(1);
    expect(screen.getByText("expected ₺35,000.00")).toBeInTheDocument();
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
  });

  it("Not yet and Skip call the API and record nothing", async () => {
    const notYet = vi.spyOn(recurringApi, "notYet").mockResolvedValue(occurrence());
    const skip = vi.spyOn(recurringApi, "skip").mockResolvedValue(occurrence());
    const confirm = vi.spyOn(recurringApi, "confirm");
    const onAnswered = vi.fn();
    withIntl(
      <ul>
        <OccurrenceRow occurrence={occurrence()} onConfirm={vi.fn()} onAnswered={onAnswered} />
      </ul>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Not yet" }));
    await waitFor(() => expect(notYet).toHaveBeenCalledWith("o1"));
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(skip).toHaveBeenCalledWith("o1"));
    expect(onAnswered).toHaveBeenCalledTimes(2);
    expect(confirm).not.toHaveBeenCalled();
  });
});

describe("confirming", () => {
  it("prefills the expectation and records what actually arrived, once", async () => {
    const confirm = vi.spyOn(recurringApi, "confirm").mockResolvedValue({
      occurrence: occurrence({ status: "confirmed", state: "confirmed" }),
      transaction: {} as never,
    });
    const onDone = vi.fn();
    withIntl(<ConfirmDialog occurrence={occurrence()} open onClose={vi.fn()} onDone={onDone} />);
    expect(await screen.findByText("Did you receive Salary?")).toBeInTheDocument();
    const amount = screen.getByLabelText("Amount received") as HTMLInputElement;
    expect(amount.value).toBe("35,000.00");
    fireEvent.change(amount, { target: { value: "34500" } });
    fireEvent.click(screen.getByRole("button", { name: "Yes, received" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(confirm).toHaveBeenCalledTimes(1);
    const [id, body, key] = confirm.mock.calls[0] ?? [];
    expect(id).toBe("o1");
    expect(body).toMatchObject({
      amount: { amount: "34500", currency: "TRY" },
      account_id: "bank",
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(changed).toHaveBeenCalled();
  });

  it("a variable amount must be entered", async () => {
    const confirm = vi.spyOn(recurringApi, "confirm");
    withIntl(
      <ConfirmDialog
        occurrence={occurrence({ expected: null, rule_name: "Freelance" })}
        open
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Yes, received" }));
    expect(await screen.findByText("Enter an amount.")).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("speaks Arabic, right to left", async () => {
    withIntl(
      <ConfirmDialog
        occurrence={occurrence({ kind: "expense", rule_name: "الإيجار" })}
        open
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
      "ar",
    );
    expect(await screen.findByText("هل دفعت الإيجار؟")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "نعم، دفعته" })).toBeInTheDocument();
  });
});
