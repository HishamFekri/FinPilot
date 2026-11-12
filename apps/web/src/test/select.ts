import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect } from "vitest";

// fireEvent already runs each event inside act(). An async act() around these never settles in
// jsdom while the popup keeps re-measuring its position, so there's none here.

/** Opens a FinPilot <Select> from the keyboard and returns its listbox. */
export async function openSelect(trigger: HTMLElement) {
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  return screen.findByRole("listbox");
}

/** Picks an option of a FinPilot <Select> by its visible text, with a full mouse press. */
export async function choose(trigger: HTMLElement, name: string | RegExp) {
  const list = await openSelect(trigger);
  const option = within(list).getByRole("option", { name });
  fireEvent.pointerDown(option, { pointerType: "mouse", buttons: 1 });
  fireEvent.mouseDown(option, { buttons: 1 });
  fireEvent.pointerUp(option, { pointerType: "mouse" });
  fireEvent.mouseUp(option);
  fireEvent.click(option);
  await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
}
