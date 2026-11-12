import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import ar from "../../../../messages/ar.json";
import en from "../../../../messages/en.json";
import type { Settlement } from "@/lib/trips";
import { SettlementStatusTag, usePaymentLine } from "./settlements";

vi.mock("../auth-provider", () => ({
  useUser: () => ({ id: "me", time_zone: "UTC" }),
  useAuth: () => ({ handleAuthError: () => false }),
}));

function settlement(overrides: Partial<Settlement>): Settlement {
  return {
    id: "s1",
    kind: "payment",
    state: "pending",
    status: "pending",
    payer: { user_id: "me", display_name: "Alice" },
    recipient: { user_id: "bob", display_name: "Bob" },
    initiated_by: { user_id: "me", display_name: "Alice" },
    amount: { amount: "25.00", currency: "EUR" },
    paid_on: "2026-11-09",
    note: null,
    reverses_settlement_id: null,
    corrects_settlement_id: null,
    reversal_id: null,
    decided_by: null,
    decided_at: null,
    decision_reason: null,
    created_at: "2026-11-09T10:00:00Z",
    waiting_on_me: false,
    can: { confirm: false, reject: false, cancel: true, reverse: false },
    ...overrides,
  };
}

function Line({ x }: { x: Settlement }) {
  const line = usePaymentLine();
  return <p>{line(x)}</p>;
}

describe("payments between members", () => {
  it("names each status in words with an icon, never colour alone", () => {
    const cases: [Partial<Settlement>, string][] = [
      [{}, "Waiting for confirmation"],
      [{ state: "confirmed", status: "confirmed" }, "Confirmed"],
      [{ state: "rejected", status: "rejected" }, "Rejected"],
      [{ state: "confirmed", status: "reversed" }, "Reversed"],
      [{ kind: "reversal", state: "pending", status: "pending" }, "Reversal waiting"],
    ];
    for (const [overrides, text] of cases) {
      const { container, unmount } = render(
        <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
          <SettlementStatusTag settlement={settlement(overrides)} />
        </NextIntlClientProvider>,
      );
      expect(screen.getByText(text)).toBeTruthy();
      expect(container.querySelector("svg")).not.toBeNull();
      unmount();
    }
  });

  it("says who paid whom, from the reader's point of view", () => {
    const { unmount } = render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Line x={settlement({})} />
      </NextIntlClientProvider>,
    );
    expect(screen.getByText(/You paid .*Bob.* .*€25\.00/)).toBeTruthy();
    unmount();
    const { container } = render(
      <NextIntlClientProvider locale="ar" messages={ar} timeZone="UTC">
        <Line
          x={settlement({
            payer: { user_id: "bob", display_name: "Bob" },
            recipient: { user_id: "me", display_name: "Alice" },
          })}
        />
      </NextIntlClientProvider>,
    );
    // Names are isolated so the Arabic sentence keeps its order around them.
    expect(container.textContent).toContain(
      `${String.fromCharCode(0x2068)}Bob${String.fromCharCode(0x2069)}`,
    );
    expect(container.textContent).toContain("لك");
  });
});
