import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import tr from "../../../messages/tr.json";
import { AuthMessage } from "./auth-message";
import { clearAuthMessage } from "./google-sign-in";
import { GoogleButton } from "./shared";

const MESSAGES = { en, ar, tr };

function show(locale: keyof typeof MESSAGES, url: string, node: ReactNode = <AuthMessage />) {
  window.history.pushState({}, "", url);
  return render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]}>
      {node}
    </NextIntlClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  act(() => clearAuthMessage());
  vi.unstubAllGlobals();
});

describe("sign-in messages on the landing page", () => {
  it.each([
    "cancelled",
    "email_not_verified",
    "account_unavailable",
    "registration_closed",
    "google_unavailable",
    "failed",
    "unreachable",
  ] as const)("explains %s in every language, with a way to retry", (code) => {
    for (const locale of ["en", "ar", "tr"] as const) {
      show(locale, `/${locale}?error=${code}`);
      expect(screen.getByRole("alert")).toHaveTextContent(MESSAGES[locale].signIn.errors[code]);
      expect(screen.getByRole("link", { name: MESSAGES[locale].signIn.retry })).toHaveAttribute(
        "href",
        expect.stringContaining("/api/v1/auth/google/start"),
      );
      cleanup();
      act(() => clearAuthMessage());
    }
  });

  it("tells a failed sign-in apart from FinPilot being unreachable", () => {
    expect(en.signIn.errors.unreachable).not.toEqual(en.signIn.errors.failed);
    expect(ar.signIn.errors.unreachable).not.toEqual(ar.signIn.errors.failed);
    expect(tr.signIn.errors.unreachable).not.toEqual(tr.signIn.errors.failed);
  });

  it("never shows what the URL says: an unknown code is the generic failure", () => {
    show("en", "/en?error=%3Cscript%3Ealert(1)%3C/script%3E");
    expect(screen.getByRole("alert")).toHaveTextContent(en.signIn.errors.failed);
    expect(screen.getByRole("alert").textContent).not.toContain("script");
  });

  it("clears the message from the address bar but keeps the destination", () => {
    show("en", "/en?next=%2Fen%2Fapp%2Fsettings&notice=expired");
    expect(screen.getByRole("status")).toHaveTextContent(en.signIn.notices.expired);
    expect(window.location.search).toBe("?next=%2Fen%2Fapp%2Fsettings");
    expect(screen.getByRole("link", { name: en.signIn.signInAgain })).toHaveAttribute(
      "href",
      "/api/v1/auth/google/start?locale=en&return_to=%2Fen%2Fapp%2Fsettings",
    );
  });

  it("ignores an unsafe destination", () => {
    show("en", "/en?next=https%3A%2F%2Fevil.example&error=failed");
    expect(screen.getByRole("link", { name: en.signIn.retry })).toHaveAttribute(
      "href",
      "/api/v1/auth/google/start?locale=en&return_to=%2Fen%2Fapp",
    );
  });

  it("shows nothing without a reason, and no longer has a signed-out message", () => {
    show("en", "/en");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    cleanup();
    show("en", "/en?notice=signed_out");
    expect(screen.queryByRole("status")).toBeNull();
    expect(window.location.search).toBe("");
  });

  it("can be dismissed", () => {
    show("ar", "/ar?error=cancelled");
    fireEvent.click(screen.getByRole("button", { name: ar.signIn.dismiss }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("stays on the page and says so when the API can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    show(
      "tr",
      "/tr",
      <>
        <GoogleButton />
        <AuthMessage />
      </>,
    );
    const button = screen.getByRole("link", { name: tr.landing.google });
    fireEvent.click(button);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(tr.signIn.errors.unreachable),
    );
    expect(window.location.pathname).toBe("/tr");
    // The button works again, so the person can retry.
    expect(button).not.toHaveAttribute("aria-disabled");
  });
});
