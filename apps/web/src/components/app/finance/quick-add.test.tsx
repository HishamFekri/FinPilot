import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ar from "../../../../messages/ar.json";
import en from "../../../../messages/en.json";
import tr from "../../../../messages/tr.json";
import type { Account } from "@/lib/accounts";
import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/auth";
import { type Category, financeApi, type Transaction, type TransactionDetail } from "@/lib/finance";
import { todayIn } from "../accounts/parts";
import { fxApi } from "@/lib/fx";
import { normalizeRate } from "./conversion";
import { lastUsed, QuickAdd } from "./quick-add";
import { signedAmount } from "./parts";
import { choose, openSelect } from "@/test/select";

const MESSAGES = { en, ar, tr } as const;

const USER: Me = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "zeynep@example.com",
  display_name: "Zeynep Kaya",
  avatar_url: null,
  locale: "en",
  preferred_currency: "TRY",
  time_zone: "Europe/Istanbul",
  status: "active",
  created_at: "2026-11-05T09:30:00Z",
};

function account(over: Partial<Account>): Account {
  return {
    id: "a0",
    name: null,
    system_key: null,
    is_default: false,
    description: null,
    type: "cash",
    currency: "TRY",
    balance: { amount: "0.00", currency: "TRY" },
    status: "active",
    opened_on: "2026-11-05",
    version: 1,
    ...over,
  };
}

function category(over: Partial<Category>): Category {
  return {
    id: "c0",
    kind: "expense",
    system_key: null,
    name: null,
    icon: "tag",
    color: "slate",
    status: "active",
    version: 1,
    last_used_at: null,
    ...over,
  };
}

const ACCOUNTS = [
  account({ id: "cash", system_key: "cash", type: "cash" }),
  account({ id: "bank", system_key: "bank", type: "bank", is_default: true }),
  account({ id: "usd", name: "Travel card", type: "wallet", currency: "USD" }),
];
const CATEGORIES = [
  category({
    id: "food",
    system_key: "food_and_drinks",
    icon: "utensils",
    last_used_at: "2026-11-01T10:00:00Z",
  }),
  category({
    id: "groceries",
    system_key: "groceries",
    icon: "shopping-basket",
    last_used_at: "2026-11-04T10:00:00Z",
  }),
  category({ id: "transport", system_key: "transportation", icon: "bus" }),
  category({
    id: "old",
    name: "Old habit",
    status: "archived",
    last_used_at: "2026-11-05T10:00:00Z",
  }),
  category({ id: "salary", kind: "income", system_key: "salary", icon: "briefcase" }),
  category({ id: "bonus", kind: "income", system_key: "bonus", icon: "award" }),
];

const changed = vi.fn(async () => {});
let finance = {
  accounts: ACCOUNTS,
  categories: CATEGORIES,
  currencies: [
    { code: "TRY", exponent: 2 },
    { code: "USD", exponent: 2 },
  ],
  defaultAccountId: "bank",
  changed,
};

vi.mock("../auth-provider", () => ({
  useUser: () => USER,
  useAuth: () => ({ handleAuthError: () => false }),
}));
vi.mock("./finance-provider", () => ({ useFinance: () => finance }));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function renderQuickAdd(
  locale: keyof typeof MESSAGES = "en",
  props: Partial<Parameters<typeof QuickAdd>[0]> = {},
) {
  return render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="UTC">
      <QuickAdd mode="inline" amountId="amount" {...props} />
    </NextIntlClientProvider>,
  );
}

function saved(body: Parameters<typeof financeApi.record>[0]): TransactionDetail {
  return {
    id: "t1",
    type: body.type,
    status: "posted",
    effective_date: body.effective_date ?? "2026-11-06",
    amount: body.amount,
    direction: null,
    account: {
      id: body.account_id,
      name: null,
      system_key: "bank",
      currency: "TRY",
      status: "active",
    },
    counter_account: null,
    counter_amount: null,
    category: CATEGORIES.find((c) => c.id === body.category_id) ?? null,
    description: body.description ?? null,
    notes: body.notes ?? null,
    created_at: "2026-11-06T10:00:00Z",
    updated_at: "2026-11-06T10:00:00Z",
    version: 1,
    changes: [{ at: "2026-11-06T10:00:00Z", action: "created" }],
  };
}

const amountInput = () => screen.getByLabelText("Amount") as HTMLInputElement;

beforeEach(() => {
  finance = { ...finance, accounts: ACCOUNTS, categories: CATEGORIES, defaultAccountId: "bank" };
  changed.mockClear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("Quick Add defaults", () => {
  it("starts as an expense, on the default account, today, with the category used last", () => {
    renderQuickAdd();
    expect(screen.getByRole("button", { name: "Expense" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Paid from")).toHaveTextContent("Bank account · TRY");
    expect(screen.getByLabelText("Date")).toHaveValue(todayIn(USER.time_zone));
    // Groceries was used most recently among active expense categories (the archived one is newer
    // but can't be chosen).
    expect(screen.getByRole("radio", { name: "Groceries" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: "Old habit" })).not.toBeInTheDocument();
  });

  it("asks for a category when none was used before", () => {
    renderQuickAdd();
    fireEvent.click(screen.getByRole("button", { name: "Income" }));
    expect(screen.getByLabelText("Received in")).toBeInTheDocument();
    for (const radio of screen.getAllByRole("radio")) expect(radio).not.toBeChecked();
  });

  it("shows default names in the reader's language", async () => {
    renderQuickAdd("tr");
    expect(screen.getByRole("radio", { name: "Market" })).toBeChecked();
    const options = within(await openSelect(screen.getByLabelText("Ödendiği hesap"))).getAllByRole(
      "option",
    );
    expect(options.map((o) => o.textContent)).toEqual([
      "Nakit · TRY",
      "Banka hesabı · TRY",
      "Travel card · USD",
    ]);
  });

  it("falls back to another account when the default is gone", () => {
    finance = { ...finance, defaultAccountId: "archived-elsewhere" };
    renderQuickAdd();
    expect(screen.getByLabelText("Paid from")).toHaveTextContent("Cash · TRY");
  });

  it("explains, rather than showing a broken form, when there is no account", () => {
    finance = { ...finance, accounts: [] };
    renderQuickAdd();
    expect(screen.getByText("Add an account first")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to accounts" })).toHaveAttribute(
      "href",
      "/app/accounts",
    );
  });
});

describe("Quick Add validation", () => {
  it("refuses an empty or unreadable amount without calling the API", () => {
    const record = vi.spyOn(financeApi, "record");
    renderQuickAdd();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Enter an amount.")).toBeInTheDocument();
    // An unclear paste is refused and explained; the field keeps what it had.
    fireEvent.change(amountInput(), { target: { value: "12.3.4" } });
    expect(screen.getByText(/isn't a clear amount/)).toBeInTheDocument();
    expect(amountInput()).toHaveValue("");
    fireEvent.change(amountInput(), { target: { value: "1.234" } });
    expect(screen.getByText("TRY has 2 decimal places.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(record).not.toHaveBeenCalled();
  });

  it("requires a category when none is preselected", () => {
    const record = vi.spyOn(financeApi, "record");
    renderQuickAdd();
    fireEvent.click(screen.getByRole("button", { name: "Income" }));
    fireEvent.change(amountInput(), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a category.");
    expect(record).not.toHaveBeenCalled();
  });

  it("shows the server's reason in the reader's language, never its raw text", async () => {
    vi.spyOn(financeApi, "record").mockRejectedValue(
      new ApiError("server", 409, "category_archived"),
    );
    renderQuickAdd("ar");
    fireEvent.change(screen.getByLabelText("المبلغ"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "حفظ" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      ar.app.transactions.errors.category_archived,
    );
    expect(changed).not.toHaveBeenCalled();
  });
});

describe("Quick Add saving", () => {
  it("saves with Enter, acknowledges, refreshes, and is ready for the next one", async () => {
    const record = vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    renderQuickAdd();
    fireEvent.change(amountInput(), { target: { value: "1,250.5" } });
    expect(screen.getByText(/Reads as/)).toHaveTextContent("₺1,250.5");
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "  Weekly shop " } });
    fireEvent.keyDown(amountInput(), { key: "Enter" });

    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    const [body, key] = record.mock.calls[0] ?? [];
    expect(body).toEqual({
      type: "expense",
      account_id: "bank",
      amount: { amount: "1250.5", currency: "TRY" },
      category_id: "groceries",
      effective_date: todayIn(USER.time_zone),
      description: "Weekly shop",
      notes: null,
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(await screen.findByText(/Saved: Groceries/)).toBeInTheDocument();
    expect(changed).toHaveBeenCalledTimes(1);
    expect(amountInput()).toHaveValue("");
    expect(amountInput()).toHaveFocus();
    // The category and account stay as they were, for the next entry.
    expect(screen.getByRole("radio", { name: "Groceries" })).toBeChecked();
  });

  it("sends the same draft once: a retry reuses its key, a changed draft gets a new one", async () => {
    const record = vi
      .spyOn(financeApi, "record")
      .mockRejectedValueOnce(new ApiError("unavailable", 0))
      .mockRejectedValueOnce(new ApiError("unavailable", 0))
      .mockRejectedValueOnce(new ApiError("unavailable", 0));
    renderQuickAdd();
    fireEvent.change(amountInput(), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(2));
    fireEvent.change(amountInput(), { target: { value: "41" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(3));
    const keys = record.mock.calls.map((call) => call[1]);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("ignores a second press while the first is still saving", async () => {
    let finish: (value: TransactionDetail) => void = () => {};
    const record = vi.spyOn(financeApi, "record").mockImplementation(
      (body) =>
        new Promise((resolve) => {
          finish = () => resolve(saved(body));
        }),
    );
    renderQuickAdd();
    fireEvent.change(amountInput(), { target: { value: "9" } });
    fireEvent.keyDown(amountInput(), { key: "Enter" });
    fireEvent.keyDown(amountInput(), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    finish(saved(record.mock.calls[0]![0]));
    await screen.findByText(/Saved:/);
    expect(record).toHaveBeenCalledTimes(1);
  });

  it("in the dialog, Shift+Enter keeps it open for another; Save closes it", async () => {
    vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    const onClose = vi.fn();
    renderQuickAdd("en", { mode: "dialog", onClose });
    fireEvent.change(amountInput(), { target: { value: "3" } });
    fireEvent.keyDown(amountInput(), { key: "Enter", shiftKey: true });
    await screen.findByText(/Saved:/);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.change(amountInput(), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("reads Arabic-Indic digits and the Arabic decimal mark", async () => {
    const record = vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    renderQuickAdd("ar");
    fireEvent.change(screen.getByLabelText("المبلغ"), { target: { value: "١٢٫٥" } });
    fireEvent.keyDown(screen.getByLabelText("المبلغ"), { key: "Enter" });
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.[0].amount).toEqual({ amount: "12.5", currency: "TRY" });
  });

  it("records income in the account's own currency", async () => {
    const record = vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    renderQuickAdd();
    fireEvent.click(screen.getByRole("button", { name: "Income" }));
    fireEvent.click(screen.getByRole("radio", { name: "Salary" }));
    await choose(screen.getByLabelText("Received in"), "Travel card · USD");
    fireEvent.change(amountInput(), { target: { value: "2000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.[0]).toMatchObject({
      type: "income",
      account_id: "usd",
      category_id: "salary",
      amount: { amount: "2000", currency: "USD" },
    });
  });
});

describe("helpers", () => {
  it("picks the most recently used active category of a kind", () => {
    expect(lastUsed(CATEGORIES, "expense")).toBe("groceries");
    expect(lastUsed(CATEGORIES, "income")).toBeNull();
  });

  it("signs amounts the way people read them, without arithmetic", () => {
    const base = saved({
      type: "expense",
      account_id: "bank",
      amount: { amount: "12.50", currency: "TRY" },
      category_id: "food",
    }) as Transaction;
    expect(signedAmount(base).amount).toBe("-12.50");
    expect(signedAmount({ ...base, type: "income" }).amount).toBe("12.50");
    expect(signedAmount({ ...base, type: "adjustment", direction: "decrease" }).amount).toBe(
      "-12.50",
    );
    expect(signedAmount({ ...base, type: "transfer" }).amount).toBe("12.50");
  });
});

describe("Quick Add picks the account for the currency", () => {
  it("records a USD expense from the USD account when no account was chosen", async () => {
    const record = vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    renderQuickAdd();
    await choose(screen.getByLabelText("Currency"), "USD");
    expect(screen.getByLabelText("Paid from")).toHaveTextContent("Travel card · USD");
    fireEvent.change(amountInput(), { target: { value: "22" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    // Same currency: nothing is converted.
    expect(record.mock.calls[0]?.[0]).toMatchObject({
      account_id: "usd",
      amount: { amount: "22", currency: "USD" },
    });
    expect(record.mock.calls[0]?.[0]).not.toHaveProperty("conversion");
  });

  it("never changes an account the person chose", async () => {
    vi.spyOn(fxApi, "convert").mockResolvedValue({
      rate: {
        id: "rate-1",
        base: "USD",
        quote: "TRY",
        rate: "50.153",
        rate_date: "2026-11-06",
        requested_date: "2026-11-06",
        source: "provider",
        provider: "frankfurter",
        upstream: "ECB",
        retrieved_at: "2026-11-06T10:00:00Z",
        age_days: 0,
        is_stale: false,
        fallback: false,
      },
      original: { amount: "22", currency: "USD" },
      converted: { amount: "1103.37", currency: "TRY" },
    });
    renderQuickAdd();
    await choose(screen.getByLabelText("Paid from"), "Cash · TRY");
    await choose(screen.getByLabelText("Currency"), "USD");
    expect(screen.getByLabelText("Paid from")).toHaveTextContent("Cash · TRY");
    fireEvent.change(amountInput(), { target: { value: "22" } });
    // The conversion step appears instead of a silent switch.
    expect(await screen.findByText(/European Central Bank reference rate/)).toBeInTheDocument();
  });
});

describe("Quick Add in another currency", () => {
  const reference = {
    rate: {
      id: "rate-1",
      base: "USD",
      quote: "TRY",
      rate: "50.153",
      rate_date: todayIn(USER.time_zone),
      requested_date: todayIn(USER.time_zone),
      source: "provider" as const,
      provider: "frankfurter",
      upstream: "ECB",
      retrieved_at: "2026-11-06T10:00:00Z",
      age_days: 0,
      is_stale: false,
      fallback: false,
    },
    original: { amount: "22", currency: "USD" },
    converted: { amount: "1103.37", currency: "TRY" },
  };

  it("shows what was entered, the estimate for the account, and the rate's source", async () => {
    const convert = vi.spyOn(fxApi, "convert").mockResolvedValue(reference);
    const record = vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    renderQuickAdd();
    // The person keeps the TRY account: then USD is converted, explicitly.
    await choose(screen.getByLabelText("Paid from"), "Bank account · TRY");
    await choose(screen.getByLabelText("Currency"), "USD");
    fireEvent.change(amountInput(), { target: { value: "22" } });
    expect(await screen.findByText(/European Central Bank reference rate/)).toBeInTheDocument();
    expect(convert).toHaveBeenCalledWith(
      expect.objectContaining({ from: "USD", to: "TRY", amount: "22" }),
    );
    expect(screen.getByText("Taken from Bank account")).toBeInTheDocument();
    expect(screen.getByText("estimate")).toBeInTheDocument();
    expect(screen.getByText("1 USD = 50.153 TRY")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.[0]).toMatchObject({
      account_id: "bank",
      amount: { amount: "22", currency: "USD" },
      conversion: { method: "reference", rate_id: "rate-1" },
    });
  });

  it("can record the amount actually charged instead", async () => {
    vi.spyOn(fxApi, "convert").mockResolvedValue(reference);
    const record = vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    renderQuickAdd();
    // The person keeps the TRY account: then USD is converted, explicitly.
    await choose(screen.getByLabelText("Paid from"), "Bank account · TRY");
    await choose(screen.getByLabelText("Currency"), "USD");
    fireEvent.change(amountInput(), { target: { value: "22" } });
    await screen.findByText(/European Central Bank/);
    fireEvent.click(screen.getByLabelText("Enter the amount charged"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose how this amount converts");
    expect(record).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Amount charged to Bank account/), {
      target: { value: "1,110.40" },
    });
    expect(screen.getByText("exact")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.[0].conversion).toEqual({
      method: "actual_amount",
      account_amount: "1110.40",
      rate_id: "rate-1",
    });
  });

  it("never invents a rate when none is available", async () => {
    vi.spyOn(fxApi, "convert").mockRejectedValue(new ApiError("server", 503, "fx_unavailable"));
    const record = vi.spyOn(financeApi, "record");
    renderQuickAdd();
    // The person keeps the TRY account: then USD is converted, explicitly.
    await choose(screen.getByLabelText("Paid from"), "Bank account · TRY");
    await choose(screen.getByLabelText("Currency"), "USD");
    fireEvent.change(amountInput(), { target: { value: "22" } });
    expect(await screen.findByText(/No exchange rate is available right now/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Use this rate")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(record).not.toHaveBeenCalled();
  });

  it("asks before using an old rate", async () => {
    vi.spyOn(fxApi, "convert").mockResolvedValue({
      ...reference,
      rate: { ...reference.rate, rate_date: "2026-10-20", age_days: 17, is_stale: true },
    });
    const record = vi.spyOn(financeApi, "record").mockImplementation(async (body) => saved(body));
    renderQuickAdd();
    // The person keeps the TRY account: then USD is converted, explicitly.
    await choose(screen.getByLabelText("Paid from"), "Bank account · TRY");
    await choose(screen.getByLabelText("Currency"), "USD");
    fireEvent.change(amountInput(), { target: { value: "22" } });
    expect(await screen.findByText(/17 days before the date of this entry/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(record).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Use this older rate anyway"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.[0].conversion).toEqual({
      method: "reference",
      rate_id: "rate-1",
      accept_stale: true,
    });
  });
});

describe("typed rates", () => {
  it("are read in any of the three languages' habits, never guessed", () => {
    expect(normalizeRate("0,92")).toBe("0.92");
    expect(normalizeRate("0.92")).toBe("0.92");
    expect(normalizeRate("٠٫٩٢")).toBe("0.92");
    expect(normalizeRate("50.153")).toBe("50.153");
    for (const bad of ["", "abc", "1.2.3", "-1", "0", "1,234.5", "1e3"]) {
      expect(normalizeRate(bad)).toBeNull();
    }
  });
});
