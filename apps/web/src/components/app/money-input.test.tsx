import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import tr from "../../../messages/tr.json";
import { MoneyInput } from "./money-input";

const MESSAGES = { en, ar, tr } as const;

function Field({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return <MoneyInput aria-label="Amount" value={value} onValueChange={setValue} />;
}

function setup(locale: keyof typeof MESSAGES, initial = "") {
  render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]}>
      <Field initial={initial} />
    </NextIntlClientProvider>,
  );
  return screen.getByLabelText("Amount") as HTMLInputElement;
}

/** Simulates the browser: the new text and the caret where the browser leaves it. */
function typeAt(input: HTMLInputElement, text: string, caret: number) {
  input.focus();
  // The prototype's setter bypasses React's value tracking, as a real keystroke does.
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, text);
  input.setSelectionRange(caret, caret);
  fireEvent.input(input);
}

describe("MoneyInput", () => {
  it("groups thousands as you type and keeps the caret after the typed digit", () => {
    const input = setup("en", "123");
    typeAt(input, "1234", 4);
    expect(input.value).toBe("1,234");
    expect(input.selectionStart).toBe(5);
    typeAt(input, "1,2934", 4); // a digit typed in the middle
    expect(input.value).toBe("12,934");
    expect(input.selectionStart).toBe(4);
  });

  it("uses Turkish marks, and refuses a typed thousands mark with a reason", () => {
    const input = setup("tr", "12");
    typeAt(input, "12.", 3);
    expect(input.value).toBe("12");
    expect(screen.getByRole("status")).toHaveTextContent("Ondalık için “,” kullan");
    typeAt(input, "12,", 3);
    expect(input.value).toBe("12,");
    typeAt(input, "12,5", 4);
    expect(input.value).toBe("12,5");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("reads Arabic-Indic digits and shows Western digits, left to right", () => {
    const input = setup("ar");
    typeAt(input, "١٢٣٤٥", 5);
    expect(input.value).toBe("12,345");
    expect(input).toHaveAttribute("dir", "ltr");
    expect(input).toHaveAttribute("inputmode", "decimal");
  });

  it("formats a pasted amount and refuses an unclear one", () => {
    const input = setup("en");
    fireEvent.change(input, { target: { value: "1234567.89" } });
    expect(input.value).toBe("1,234,567.89");
    fireEvent.change(input, { target: { value: "1.234,56" } });
    expect(input.value).toBe("1,234,567.89");
    expect(screen.getByRole("status")).toHaveTextContent("isn't a clear amount");
  });
});
