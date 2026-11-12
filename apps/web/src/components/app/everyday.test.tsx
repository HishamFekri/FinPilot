import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import { isAppPage } from "@/lib/app-routes";
import type { Goal } from "@/lib/everyday";
import type { Transaction } from "@/lib/finance";
import { flow } from "./finance/parts";
import { TemplateStrip } from "./finance/templates";
import { GoalBar } from "./goals/goals-view";

const { openWith, record, coffee } = vi.hoisted(() => ({
  openWith: vi.fn(),
  record: vi.fn(),
  coffee: {
    id: "t1",
    name: "Coffee",
    kind: "expense" as const,
    amount: { amount: "3.50", currency: "EUR" },
    currency: "EUR",
    account_id: null,
    category_id: "c1",
    description: null,
    is_favourite: true,
    status: "active" as const,
    use_count: 0,
    last_used_at: null,
    version: 1,
  },
}));
vi.mock("./auth-provider", () => ({
  useUser: () => ({ id: "me", time_zone: "UTC" }),
  useAuth: () => ({ handleAuthError: () => false }),
}));
vi.mock("./finance/finance-provider", () => ({
  useFinance: () => ({
    revision: 0,
    categories: [],
    accounts: [],
    quickAdd: { openWith, open: false, setOpen: () => undefined, preset: null },
  }),
  FinanceGate: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("@/lib/finance", async (original) => ({
  ...(await original<typeof import("@/lib/finance")>()),
  financeApi: { record },
}));

vi.mock("@/lib/everyday", async (original) => ({
  ...(await original<typeof import("@/lib/everyday")>()),
  templatesApi: {
    list: vi.fn(async () => [coffee, { ...coffee, id: "t2", name: "Rent", is_favourite: false }]),
  },
}));

describe("everyday features", () => {
  it("has pages for the calendar, goals, subscriptions and templates", () => {
    for (const path of ["/calendar", "/goals", "/subscriptions", "/settings/templates"]) {
      expect(isAppPage(path)).toBe(true);
    }
  });

  it("shows a refund as money in", () => {
    expect(flow({ type: "refund" } as Transaction)).toBe("in");
  });

  it("never draws a goal past its target, and colours a reached one differently", () => {
    const goal = {
      percent: "120.0",
      reached: true,
    } as Goal;
    const { container } = render(<GoalBar goal={goal} />);
    const fill = container.querySelector("span > span") as HTMLElement;
    expect(fill.style.inlineSize).toBe("100%");
    expect(fill.dataset.done).toBe("true");
  });

  it("a favourite template only opens Quick Add filled in; it never saves anything", async () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <TemplateStrip />
      </NextIntlClientProvider>,
    );
    const chip = await screen.findByRole("button", { name: /Coffee/ });
    expect(screen.queryByText("Rent")).toBeNull(); // not a favourite
    fireEvent.click(chip);
    expect(openWith).toHaveBeenCalledWith(
      expect.objectContaining({ templateId: "t1", amount: "3.50", categoryId: "c1" }),
    );
    expect(record).not.toHaveBeenCalled();
  });

  it("reads in Arabic", async () => {
    render(
      <NextIntlClientProvider locale="ar" messages={ar} timeZone="UTC">
        <TemplateStrip />
      </NextIntlClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("إدارة القوالب")).toBeTruthy());
  });
});
