import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../messages/en.json";
import { ThemeProvider } from "./theme-provider";
import { ThemeToggle } from "./theme-toggle";

function stubSystemTheme(dark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches: dark,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  );
}

function renderToggle() {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>
    </NextIntlClientProvider>,
  );
  return screen.getByRole("button", { name: en.shell.toggleTheme });
}

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
  });

  it("follows the system preference until a choice is made", async () => {
    stubSystemTheme(true);
    renderToggle();
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    expect(localStorage.getItem("fp_theme")).toBeNull();
  });

  it("switches and remembers the choice", async () => {
    stubSystemTheme(false);
    const button = renderToggle();
    await waitFor(() => expect(document.documentElement).toHaveClass("light"));

    fireEvent.click(button);
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    expect(localStorage.getItem("fp_theme")).toBe("dark");

    fireEvent.click(button);
    await waitFor(() => expect(document.documentElement).toHaveClass("light"));
    expect(localStorage.getItem("fp_theme")).toBe("light");
  });

  it("a stored choice beats the system preference", async () => {
    localStorage.setItem("fp_theme", "light");
    stubSystemTheme(true);
    renderToggle();
    await waitFor(() => expect(document.documentElement).toHaveClass("light"));
  });
});
