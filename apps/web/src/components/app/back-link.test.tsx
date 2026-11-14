import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";
import en from "../../../messages/en.json";
import { BackLink } from "./back-link";

const depth = vi.hoisted(() => ({ inApp: false }));
vi.mock("@/lib/history-depth", () => ({ canGoBackInApp: () => depth.inApp }));
vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  usePathname: () => "/en/app/trips/1",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

function back() {
  render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <BackLink href="/app/trips">Trips</BackLink>
    </NextIntlClientProvider>,
  );
  return screen.getByRole("link", { name: "Trips" });
}

afterEach(() => {
  depth.inApp = false;
  vi.restoreAllMocks();
});

describe("BackLink", () => {
  it("is a real link to the parent page, in the current language", () => {
    expect(back()).toHaveAttribute("href", "/en/app/trips");
  });

  it("steps back through history when an in-app page is behind this one", () => {
    depth.inApp = true;
    const historyBack = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const link = back();
    const event = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    fireEvent(link, event);
    expect(historyBack).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it("opens the parent page when nothing in-app is behind it (a direct link)", () => {
    const historyBack = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const link = back();
    fireEvent.click(link);
    expect(historyBack).not.toHaveBeenCalled();
  });

  it("leaves a modified click (new tab) to the browser", () => {
    depth.inApp = true;
    const historyBack = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const link = back();
    const event = new MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true });
    fireEvent(link, event);
    expect(historyBack).not.toHaveBeenCalled();
  });
});
