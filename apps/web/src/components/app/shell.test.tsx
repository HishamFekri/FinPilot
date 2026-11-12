import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";
import en from "../../../messages/en.json";
import type { Me } from "@/lib/auth";
import { AppShell } from "./app-shell";
import { pageWidth } from "./nav";

let pathname = "/en/app";
vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  usePathname: () => pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

const USER: Me = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "hisham@example.com",
  display_name: "Hisham Jouda",
  avatar_url: null,
  locale: "en",
  preferred_currency: "EUR",
  time_zone: "Europe/Istanbul",
  status: "active",
  created_at: "2026-11-05T09:30:00Z",
};

function shell(path = "/en/app") {
  pathname = path;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url === "/api/v1/auth/me" ? USER : [];
      return new Response(JSON.stringify(body), {
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <AppShell>
        <h1>Page</h1>
      </AppShell>
    </NextIntlClientProvider>,
  );
}

afterEach(() => {
  pathname = "/en/app";
});

describe("app shell", () => {
  it("shows FinPilot as a text-only wordmark", async () => {
    shell();
    await screen.findByRole("heading", { name: "Page" });
    const brands = screen.getAllByRole("link", { name: "FinPilot" });
    expect(brands.length).toBeGreaterThan(0);
    for (const brand of brands) {
      expect(brand).toHaveTextContent(/^FinPilot$/);
      expect(brand.querySelector("svg, img")).toBeNull();
    }
  });

  it("puts settings behind the profile entry instead of a separate navigation link", async () => {
    shell();
    await screen.findByRole("heading", { name: "Page" });
    const [sidebarNav] = screen.getAllByRole("navigation", { name: "Main navigation" }) as [
      HTMLElement,
    ];
    expect(within(sidebarNav).queryByRole("link", { name: /Settings/ })).toBeNull();
    // The profile entry, with the person's name, is there instead.
    expect(
      screen.getAllByRole("button", { name: "Account menu: Hisham Jouda" }).length,
    ).toBeGreaterThan(0);
  });

  it("has no page title in the top bar: the page's own heading is the only one", async () => {
    shell("/en/app/transactions");
    await screen.findByRole("heading", { name: "Page" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("banner")).not.toHaveTextContent("Transactions");
  });

  it("gives each page a centred width that suits it", async () => {
    shell("/en/app/transactions");
    await screen.findByRole("heading", { name: "Page" });
    expect(screen.getByRole("main")).toHaveAttribute("data-width", "wide");
  });
});

describe("page widths", () => {
  it.each([
    ["/app", "wide"],
    ["/app/settings", "reading"],
    ["/app/settings/sessions", "reading"],
    ["/app/transactions", "wide"],
    ["/app/analytics", "wide"],
    ["/app/installments", "standard"],
    ["/app/trips", "standard"],
    ["/app/trips/3f2c", "trip"],
    ["/app/trips/3f2c/report", "trip"],
    ["/app/accounts", "standard"],
  ])("%s is %s", (path, width) => {
    expect(pageWidth(path)).toBe(width);
  });
});
