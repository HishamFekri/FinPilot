import { render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import tr from "../../../messages/tr.json";
import type { Me } from "@/lib/auth";
import { PLANNED_SECTIONS } from "@/lib/app-routes";
import { AuthProvider, useAuth } from "./auth-provider";
import { NAV } from "./nav";
import { changesOf, validateDraft } from "./settings-view";

const MESSAGES = { en, ar, tr } as const;

// Only the hooks these components touch are replaced; the rest is Next.js's own.
vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  usePathname: () => "/en/app",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

function withIntl(locale: keyof typeof MESSAGES, node: ReactNode) {
  return render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]}>
      {node}
    </NextIntlClientProvider>,
  );
}

const USER: Me = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "zeynep@example.com",
  display_name: "Zeynep Kaya",
  avatar_url: null,
  locale: "en",
  preferred_currency: null,
  time_zone: "Europe/Istanbul",
  status: "active",
  created_at: "2026-11-05T09:30:00Z",
};

function Probe() {
  const { status, user } = useAuth();
  return (
    <p>
      {status}
      {user ? ` ${user.email}` : ""}
    </p>
  );
}

describe("session state", () => {
  it("is signed in only once the API confirms it", async () => {
    let answer: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            answer = resolve;
          }),
      ),
    );
    withIntl(
      "en",
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByText("checking")).toBeInTheDocument();

    answer(new Response(JSON.stringify(USER), { headers: { "content-type": "application/json" } }));
    await waitFor(() =>
      expect(screen.getByText("signed-in zeynep@example.com")).toBeInTheDocument(),
    );
  });

  it("keeps the session when the API can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    withIntl(
      "en",
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText("unavailable")).toBeInTheDocument());
    // Only the profile request was made: no logout, so nothing was signed out.
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(["/api/v1/auth/me"]);
  });
});

describe("profile form rules", () => {
  const draft = {
    display_name: "Zeynep Kaya",
    locale: "en" as const,
    preferred_currency: "",
    time_zone: "Europe/Istanbul",
  };

  it("mirrors the API's checks", () => {
    expect(validateDraft(draft)).toEqual({});
    expect(validateDraft({ ...draft, display_name: "   " })).toEqual({ display_name: "required" });
    expect(validateDraft({ ...draft, display_name: "x".repeat(101) })).toEqual({
      display_name: "tooLong",
    });
    expect(validateDraft({ ...draft, display_name: "Ali\u202Ebad" })).toEqual({
      display_name: "characters",
    });
    expect(validateDraft({ ...draft, preferred_currency: "eur" })).toEqual({
      preferred_currency: "invalid",
    });
  });

  it("sends only what changed, in the API's shape", () => {
    expect(changesOf(USER, draft)).toEqual({});
    expect(
      changesOf(USER, { ...draft, display_name: "  Zeynep K ", preferred_currency: "TRY" }),
    ).toEqual({ display_name: "Zeynep K", preferred_currency: "TRY" });
    expect(changesOf({ ...USER, preferred_currency: "EUR" }, draft)).toEqual({
      preferred_currency: null,
    });
  });
});

describe("navigation", () => {
  it("lists exactly the sections that have a placeholder page", () => {
    expect(NAV.filter((item) => !item.built).map((item) => item.path.slice(1))).toEqual([
      ...PLANNED_SECTIONS,
    ]);
  });
});
