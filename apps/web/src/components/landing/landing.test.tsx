import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import { CreatorCredit } from "./creator";
import { SiteFooter } from "./site-footer";
import { parseAmount, Record } from "./record";
import { googleStartHref } from "@/lib/auth";
import { GoogleButton } from "./shared";

function withIntl(locale: "en" | "ar", node: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : ar}>
      {node}
    </NextIntlClientProvider>,
  );
}

describe("amount parsing in the Quick Add demo", () => {
  it.each([
    ["12.50", "en", 12.5],
    ["1,234.5", "en", 1234.5],
    ["1.234,5", "tr", 1234.5],
    ["12,5", "tr", 12.5],
    ["١٢٫٥", "ar", 12.5],
    ["١٬٢٣٤", "ar", 1234],
  ])("reads %s in %s as %d", (raw, locale, expected) => {
    expect(parseAmount(raw, locale)).toBe(expected);
  });

  it.each([
    ["", "en"],
    ["abc", "en"],
    ["0", "en"],
    ["-5", "en"],
    ["1,2,3", "tr"],
    ["12.345", "en"],
  ])("refuses %s", (raw, locale) => {
    expect(parseAmount(raw, locale)).toBeNull();
  });
});

describe("Continue with Google", () => {
  it("goes to the real sign-in route with the page's language, and nothing else", () => {
    expect(googleStartHref("ar")).toBe("/api/v1/auth/google/start?locale=ar&return_to=%2Far%2Fapp");
    withIntl("ar", <GoogleButton />);
    const link = screen.getByRole("link", { name: ar.landing.google });
    expect(link).toHaveAttribute(
      "href",
      "/api/v1/auth/google/start?locale=ar&return_to=%2Far%2Fapp",
    );
  });
});

describe("creator credit", () => {
  it("is one link to the creator's site, with their own supplied logo and both lines of text", () => {
    withIntl("en", <CreatorCredit />);
    const link = screen.getByRole("link", { name: /Hisham Jouda/ });
    expect(link).toHaveAttribute("href", "https://hishamjouda.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("By");
    const logo = link.querySelector("img");
    expect(logo).toHaveAttribute("src", "/brand/hisham-jouda-logo.png");
    expect(logo).toHaveAttribute("alt", "");
  });
});

describe("site footer", () => {
  it("links GitHub and LinkedIn in new tabs and shows the copyright", () => {
    withIntl("en", <SiteFooter />);
    const linkedIn = screen.getByRole("link", { name: "Hisham Jouda on LinkedIn" });
    expect(linkedIn).toHaveAttribute("href", "https://www.linkedin.com/in/hishamjouda");
    expect(linkedIn).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Hisham Jouda on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/HishamFekri",
    );
    expect(screen.getByText("© 2026 FinPilot")).toBeInTheDocument();
  });
});

describe("Quick Add demo", () => {
  it("adds a valid amount and explains an invalid one, saving nothing", () => {
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("matchMedia", () => ({
      matches: true,
      addEventListener() {},
      removeEventListener() {},
    }));
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    withIntl("en", <Record />);

    const input = screen.getByLabelText(en.landing.record.demo.amount);
    fireEvent.change(input, { target: { value: "abc" } });
    fireEvent.submit(input.closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent(en.landing.record.demo.invalid);
    expect(input).toHaveAttribute("aria-invalid", "true");

    fireEvent.change(input, { target: { value: "12.5" } });
    fireEvent.submit(input.closest("form")!);
    expect(screen.getByRole("status")).toHaveTextContent(
      "€12.50 added to Groceries in this example. Nothing is saved.",
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
