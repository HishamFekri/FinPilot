import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import ar from "../../../../messages/ar.json";
import en from "../../../../messages/en.json";
import tr from "../../../../messages/tr.json";
import type { Budget } from "@/lib/analytics";
import { BudgetMeter, BudgetState } from "./budgets-view";
import { CategoryBars } from "./charts";

const MESSAGES = { en, ar, tr } as const;

function wrap(locale: keyof typeof MESSAGES, children: ReactNode) {
  return (
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="UTC">
      {children}
    </NextIntlClientProvider>
  );
}

const money = (amount: string) => ({ amount, currency: "TRY" });

function budget(spent: string, state: Budget["state"], percent: string): Budget {
  return {
    category: null,
    currency: "TRY",
    limit: money("900.00"),
    spent: money(spent),
    remaining: money("0.00"),
    percent,
    state,
    alert_percent: 80,
    effective_from: "2026-11-01",
    not_counted: [],
  };
}

describe("budget state", () => {
  it("says the state in words, not only with colour", () => {
    render(wrap("en", <BudgetState budget={budget("1025.50", "exceeded", "113.9")} />));
    expect(screen.getByText("Over budget · 113.9%")).toBeTruthy();
  });

  it("is translated, with the reader's digits", () => {
    const { container } = render(
      wrap("tr", <BudgetState budget={budget("720.00", "approaching", "80.5")} />),
    );
    expect(container.textContent).toBe("Limite yaklaşıyor · %80,5");
  });

  it("draws a full, textured bar when the limit is exceeded and marks the warning level", () => {
    const { container } = render(
      wrap("en", <BudgetMeter budget={budget("1025.50", "exceeded", "113.9")} />),
    );
    const meter = container.firstElementChild as HTMLElement;
    expect(meter.getAttribute("aria-hidden")).toBe("true");
    const [fill, tick] = Array.from(meter.children) as HTMLElement[];
    expect(fill?.dataset.state).toBe("exceeded");
    expect(fill?.style.inlineSize).toBe("100%");
    expect(tick?.style.insetInlineStart).toBe("80%");
  });
});

describe("category bars", () => {
  it("writes every amount and share beside its bar", () => {
    render(
      wrap(
        "en",
        <CategoryBars
          items={[
            {
              category: null,
              amount: money("1200.00"),
              percent: "75.0",
              previous: money("0.00"),
            },
          ]}
        />,
      ),
    );
    expect(screen.getByText("No category")).toBeTruthy();
    expect(screen.getByText("75%")).toBeTruthy();
  });
});
