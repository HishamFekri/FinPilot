import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import ar from "../../../../messages/ar.json";
import en from "../../../../messages/en.json";
import tr from "../../../../messages/tr.json";
import { isAppPage } from "@/lib/app-routes";
import { inviteLink, TRIP_CATEGORIES } from "@/lib/trips";
import { ExpenseStatusTag, isolate } from "./parts";

const MESSAGES = { en, ar, tr } as const;

describe("trip routes and links", () => {
  it("knows the trip pages and refuses anything else", () => {
    expect(isAppPage("/trips")).toBe(true);
    expect(isAppPage("/trips/join")).toBe(true);
    expect(isAppPage("/trips/42d1eb68-fbb6-4de1-a602-215dec24987a")).toBe(true);
    expect(isAppPage("/trips/not-a-uuid")).toBe(false);
    expect(isAppPage("/trips/42d1eb68-fbb6-4de1-a602-215dec24987a/extra")).toBe(false);
  });

  it("keeps the invitation token in the fragment, never the path or query", () => {
    const link = inviteLink("https://finpilot.example", "ar", "tok_123");
    expect(link).toBe("https://finpilot.example/ar/invite#tok_123");
    expect(new URL(link).search).toBe("");
  });

  it("names every trip category in all three languages", () => {
    for (const messages of Object.values(MESSAGES)) {
      const defaults = messages.app.categories.defaults as Record<string, string>;
      for (const key of TRIP_CATEGORIES) expect(defaults[key]).toBeTruthy();
    }
  });
});

describe("expense status", () => {
  it("says a waiting expense in words, with an icon, in each language", () => {
    for (const [locale, expected] of [
      ["en", "Waiting for payer"],
      ["ar", "بانتظار الدافع"],
      ["tr", "Ödeyeni bekliyor"],
    ] as const) {
      const { unmount, container } = render(
        <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="UTC">
          <ExpenseStatusTag
            expense={{
              status: "pending_payer",
              has_pending_correction: false,
              is_corrected: false,
            }}
          />
        </NextIntlClientProvider>,
      );
      expect(screen.getByText(expected)).toBeTruthy();
      expect(container.querySelector("svg")).not.toBeNull();
      unmount();
    }
  });

  it("shows nothing extra for a plain approved expense", () => {
    const { container } = render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ExpenseStatusTag
          expense={{ status: "approved", has_pending_correction: false, is_corrected: false }}
        />
      </NextIntlClientProvider>,
    );
    expect(container.textContent).toBe("");
  });

  it("isolates a name so it can't reorder the sentence around it", () => {
    const wrapped = isolate("Alice");
    expect(wrapped.codePointAt(0)).toBe(0x2068);
    expect(wrapped.codePointAt(wrapped.length - 1)).toBe(0x2069);
    expect(wrapped.slice(1, -1)).toBe("Alice");
  });
});
