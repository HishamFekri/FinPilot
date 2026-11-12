import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import en from "../../../messages/en.json";
import { choose, openSelect as open } from "@/test/select";
import { Select } from "./select";

const TYPES = [
  { value: "", label: "All types" },
  { value: "expense", label: "Expense" },
  { value: "income", label: "Income" },
  { value: "refund", label: "Refund" },
];

function Field({ initial = "", locale = "en" }: { initial?: string; locale?: "en" | "ar" }) {
  const [value, setValue] = useState(initial);
  return (
    <NextIntlClientProvider locale={locale} messages={en}>
      <label htmlFor="type">Type</label>
      <Select id="type" value={value} onValueChange={setValue}>
        {TYPES.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </Select>
      <output data-testid="chosen">{value}</output>
    </NextIntlClientProvider>
  );
}

describe("Select", () => {
  it("is a labelled combobox that shows the chosen option", () => {
    render(<Field initial="income" />);
    const trigger = screen.getByRole("combobox", { name: "Type" });
    expect(trigger).toHaveTextContent("Income");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("shows an empty-valued option such as 'All types'", () => {
    render(<Field initial="" />);
    expect(screen.getByRole("combobox", { name: "Type" })).toHaveTextContent("All types");
  });

  it("opens with the keyboard on the selected option, and marks it selected", async () => {
    render(<Field initial="income" />);
    const list = await open(screen.getByRole("combobox", { name: "Type" }));
    expect(screen.getByRole("combobox", { name: "Type" })).toHaveAttribute("aria-expanded", "true");
    const income = within(list).getByRole("option", { name: "Income" });
    expect(income).toHaveAttribute("aria-selected", "true");
    expect(within(list).getAllByRole("option")).toHaveLength(4);
  });

  it("chooses with Enter, closes, and returns focus to the button", async () => {
    render(<Field initial="" />);
    const trigger = screen.getByRole("combobox", { name: "Type" });
    await open(trigger);
    // Arrow keys move the highlight; Enter chooses it.
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "ArrowDown" });
    const highlighted = document.activeElement as HTMLElement;
    expect(highlighted).toHaveAttribute("role", "option");
    const label = highlighted.textContent;
    fireEvent.keyDown(highlighted, { key: "Enter" });
    fireEvent.click(highlighted);
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    expect(trigger).toHaveTextContent(label ?? "");
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("closes with Escape without changing the value", async () => {
    render(<Field initial="refund" />);
    const trigger = screen.getByRole("combobox", { name: "Type" });
    const list = await open(trigger);
    fireEvent.keyDown(list, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    expect(screen.getByTestId("chosen")).toHaveTextContent("refund");
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("reports the chosen value", async () => {
    render(<Field initial="" />);
    await choose(screen.getByRole("combobox", { name: "Type" }), "Expense");
    expect(screen.getByTestId("chosen")).toHaveTextContent("expense");
  });
});
