import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../../../messages/en.json";
import type { Account } from "@/lib/accounts";
import type { Me } from "@/lib/auth";
import type { Category, TransactionDetail } from "@/lib/finance";
import { TransactionDialog, useTxnDialog } from "./transaction-dialog";

/**
 * Regression tests for the financial actions in a transaction's dialog (everyday milestone).
 *
 * The browser run found that clicking "Record refund" recorded a full refund at once: React
 * reused the footer's <button> element for the refund view's "Save refund" (type=submit), and
 * the browser ran the click's default action (submitting the form) after the view had changed.
 * jsdom does the same, so these tests reproduce it against the real component. Every mutating
 * API call is a spy: "the database is unchanged" means none of them was called.
 */

const api = vi.hoisted(() => ({
  get: vi.fn(),
  refund: vi.fn(),
  correct: vi.fn(),
  void: vi.fn(),
  record: vi.fn(),
  template: vi.fn(),
}));
vi.mock("@/lib/finance", async (original) => ({
  ...(await original<typeof import("@/lib/finance")>()),
  financeApi: {
    get: api.get,
    refund: api.refund,
    correct: api.correct,
    void: api.void,
    record: api.record,
  },
}));
vi.mock("@/lib/everyday", async (original) => ({
  ...(await original<typeof import("@/lib/everyday")>()),
  templatesApi: { create: api.template },
}));

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
const BANK: Account = {
  id: "bank",
  name: null,
  system_key: "bank",
  is_default: true,
  description: null,
  type: "bank",
  currency: "TRY",
  balance: { amount: "1000.00", currency: "TRY" },
  status: "active",
  opened_on: "2026-11-01",
  version: 1,
};
const GROCERIES: Category = {
  id: "groceries",
  kind: "expense",
  system_key: "groceries",
  name: null,
  icon: "shopping-basket",
  color: "green",
  status: "active",
  version: 1,
  last_used_at: null,
};
const changed = vi.fn(async () => {});

vi.mock("../auth-provider", () => ({
  useUser: () => USER,
  useAuth: () => ({ handleAuthError: () => false }),
}));
vi.mock("./finance-provider", () => ({
  useFinance: () => ({
    accounts: [BANK],
    categories: [GROCERIES],
    currencies: [{ code: "TRY", exponent: 2 }],
    defaultAccountId: "bank",
    changed,
  }),
}));
vi.mock("../app-shell", () => ({ usePortalRoot: () => ({ current: document.body }) }));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

/** A posted 150.00 TRY grocery purchase, nothing refunded yet. */
function purchase(over: Partial<TransactionDetail> = {}): TransactionDetail {
  return {
    id: "t1",
    type: "expense",
    status: "posted",
    effective_date: "2026-11-02",
    amount: { amount: "150.00", currency: "TRY" },
    direction: null,
    account: { id: "bank", name: null, system_key: "bank", currency: "TRY", status: "active" },
    counter_account: null,
    counter_amount: null,
    category: GROCERIES,
    description: "Market",
    notes: null,
    created_at: "2026-11-02T10:00:00Z",
    updated_at: "2026-11-02T10:00:00Z",
    version: 3,
    changes: [{ at: "2026-11-02T10:00:00Z", action: "created" }],
    refunds: {
      refunded: { amount: "0.00", currency: "TRY" },
      refundable: { amount: "150.00", currency: "TRY" },
      items: [],
    },
    ...over,
  };
}

const MUTATIONS = [api.refund, api.correct, api.void, api.record, api.template];
const unchanged = () => {
  for (const spy of MUTATIONS) expect(spy).not.toHaveBeenCalled();
};

function Harness() {
  const dialog = useTxnDialog();
  return (
    <>
      <button type="button" onClick={() => dialog.show("t1")}>
        open entry
      </button>
      <TransactionDialog dialog={dialog} />
    </>
  );
}

async function openEntry() {
  render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <Harness />
    </NextIntlClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "open entry" }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByText("Market");
  return dialog;
}

const button = (dialog: HTMLElement, name: string) => within(dialog).getByRole("button", { name });

beforeEach(() => {
  for (const spy of Object.values(api)) spy.mockReset();
  api.get.mockImplementation(async () => purchase());
  changed.mockClear();
});

describe("financial actions in a transaction's dialog", () => {
  it("1. “Record refund” opens the refund form and records nothing", async () => {
    const dialog = await openEntry();
    fireEvent.click(button(dialog, "Record refund"));
    await within(dialog).findByText("Record a refund");
    expect(within(dialog).getByLabelText("Refund amount (TRY)")).toHaveValue("150.00");
    expect(button(dialog, "Save refund")).toBeTruthy();
    unchanged();
  });

  it("2. “Correct” opens the correction form and changes nothing", async () => {
    const dialog = await openEntry();
    fireEvent.click(button(dialog, "Correct"));
    await within(dialog).findByText("Correct this entry");
    expect(button(dialog, "Save correction")).toBeTruthy();
    unchanged();
  });

  it("3. leaving either form without saving changes nothing", async () => {
    const dialog = await openEntry();
    // Refund form: edit the amount, then go back.
    fireEvent.click(button(dialog, "Record refund"));
    fireEvent.change(await within(dialog).findByLabelText("Refund amount (TRY)"), {
      target: { value: "20" },
    });
    fireEvent.click(button(dialog, "Back"));
    await within(dialog).findByText("Market");
    // Correction form: open it, then go back.
    fireEvent.click(button(dialog, "Correct"));
    await within(dialog).findByText("Correct this entry");
    fireEvent.click(button(dialog, "Back"));
    await within(dialog).findByText("Market");
    // Void: asked first; "Keep it" goes back.
    fireEvent.click(button(dialog, "Void"));
    await within(dialog).findByText("Void this entry?");
    fireEvent.click(button(dialog, "Keep it"));
    await within(dialog).findByText("Market");
    // Close the dialog from a form, too.
    fireEvent.click(button(dialog, "Record refund"));
    await within(dialog).findByText("Record a refund");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    unchanged();
    expect(changed).not.toHaveBeenCalled();
  });

  it("4. a refund is recorded only when Save is pressed, and a void only when confirmed", async () => {
    api.refund.mockImplementation(async () =>
      purchase({
        refunds: {
          refunded: { amount: "20.00", currency: "TRY" },
          refundable: { amount: "130.00", currency: "TRY" },
          items: [],
        },
      }),
    );
    api.void.mockImplementation(async () => purchase({ status: "reversed", version: 4 }));
    const dialog = await openEntry();
    fireEvent.click(button(dialog, "Record refund"));
    fireEvent.change(await within(dialog).findByLabelText("Refund amount (TRY)"), {
      target: { value: "20" },
    });
    expect(api.refund).not.toHaveBeenCalled();
    fireEvent.click(button(dialog, "Save refund"));
    await waitFor(() => expect(api.refund).toHaveBeenCalledTimes(1));
    const [id, body, key] = api.refund.mock.calls[0] as [string, { amount: object }, string];
    expect(id).toBe("t1");
    expect(body.amount).toEqual({ amount: "20", currency: "TRY" });
    expect(key).toMatch(/.{8,}/);
    await within(dialog).findByText("Refund recorded. The purchase stays as it was.");

    // Void: the first press only asks.
    fireEvent.click(button(dialog, "Void"));
    await within(dialog).findByText("Void this entry?");
    expect(api.void).not.toHaveBeenCalled();
    fireEvent.click(button(dialog, "Void entry"));
    await waitFor(() => expect(api.void).toHaveBeenCalledTimes(1));
    expect(api.void).toHaveBeenCalledWith("t1", 3);
  });

  it("4b. a correction with no change is refused without calling the server", async () => {
    const dialog = await openEntry();
    fireEvent.click(button(dialog, "Correct"));
    await within(dialog).findByText("Correct this entry");
    fireEvent.click(button(dialog, "Save correction"));
    await within(dialog).findByText("Nothing changed.");
    expect(api.correct).not.toHaveBeenCalled();
  });

  it("5. double-clicking Save posts once (and a retry reuses the same key)", async () => {
    let finish: (value: TransactionDetail) => void = () => undefined;
    api.refund.mockImplementation(
      () => new Promise<TransactionDetail>((resolve) => (finish = resolve)),
    );
    api.void.mockImplementation(() => new Promise(() => undefined));
    const dialog = await openEntry();
    fireEvent.click(button(dialog, "Record refund"));
    fireEvent.change(await within(dialog).findByLabelText("Refund amount (TRY)"), {
      target: { value: "20" },
    });
    const save = button(dialog, "Save refund");
    fireEvent.click(save);
    fireEvent.click(save);
    // A third attempt through the form itself (Enter in a field) is ignored while saving.
    fireEvent.submit(document.getElementById(save.getAttribute("form") ?? "") as HTMLFormElement);
    expect(api.refund).toHaveBeenCalledTimes(1);
    expect(save).toBeDisabled();
    finish(purchase());
    await screen.findByText("Refund recorded. The purchase stays as it was.");

    // Voiding: a double press on the confirmation voids once. (The dialog re-renders after the
    // save, so it's looked up again.)
    const live = await screen.findByRole("dialog");
    fireEvent.click(await within(live).findByRole("button", { name: "Void" }));
    const confirm = await within(live).findByRole("button", { name: "Void entry" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(api.void).toHaveBeenCalledTimes(1);
  });

  it("6. keyboard activation opens forms without posting; Enter in the form submits once", async () => {
    api.refund.mockImplementation(async () => purchase());
    const dialog = await openEntry();
    const open = button(dialog, "Record refund");
    open.focus();
    // A keyboard activation (Enter or Space on a button) is a click with detail 0.
    fireEvent.click(open, { detail: 0 });
    const amount = await within(dialog).findByLabelText("Refund amount (TRY)");
    unchanged();
    // Escape inside the form doesn't post either.
    fireEvent.keyDown(amount, { key: "Escape" });
    unchanged();
  });

  it("6b. pressing Enter in the refund amount submits the form once", async () => {
    api.refund.mockImplementation(async () => purchase());
    const dialog = await openEntry();
    fireEvent.click(button(dialog, "Record refund"), { detail: 0 });
    const amount = await within(dialog).findByLabelText("Refund amount (TRY)");
    fireEvent.change(amount, { target: { value: "15" } });
    // Implicit submission (Enter in a field) is the form's submit event.
    fireEvent.submit(amount.closest("form") as HTMLFormElement);
    await waitFor(() => expect(api.refund).toHaveBeenCalledTimes(1));
  });

  it("7. switching views never reuses a button as the next view's submit", async () => {
    const dialog = await openEntry();
    const opener = button(dialog, "Record refund");
    fireEvent.click(opener);
    await within(dialog).findByText("Record a refund");
    // The element that was clicked is gone, not turned into "Save refund".
    expect(opener.isConnected).toBe(false);
    const save = button(dialog, "Save refund");
    expect(save).not.toBe(opener);
    unchanged();
    // Back, then Correct: the old Save refund is gone, and Correct didn't post a correction.
    fireEvent.click(button(dialog, "Back"));
    await within(dialog).findByText("Market");
    expect(save.isConnected).toBe(false);
    const correct = button(dialog, "Correct");
    fireEvent.click(correct);
    await within(dialog).findByText("Correct this entry");
    expect(correct.isConnected).toBe(false);
    // Void then Keep, then Record refund again: still nothing posted.
    fireEvent.click(button(dialog, "Back"));
    fireEvent.click(await within(dialog).findByRole("button", { name: "Void" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "Keep it" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "Record refund" }));
    await within(dialog).findByText("Record a refund");
    unchanged();
  });
});
