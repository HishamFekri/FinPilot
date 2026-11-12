import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../../messages/en.json";
import ar from "../../../messages/ar.json";
import tr from "../../../messages/tr.json";
import type { Me } from "@/lib/auth";

const user: Me = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "sam@example.com",
  display_name: "Sam",
  avatar_url: null,
  locale: "en",
  preferred_currency: "EUR",
  time_zone: "UTC",
  status: "active",
  created_at: "2026-11-05T09:30:00Z",
};

const refresh = vi.fn(async () => {});
vi.mock("./auth-provider", () => ({
  useUser: () => user,
  useAuth: () => ({ refresh, handleAuthError: () => false }),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import { PrivacyView } from "./privacy-view";

type State = {
  status: string;
  requested_at: string | null;
  recent_sign_in: boolean;
  can_request: boolean;
  blockers: { kind: string; trip_id: string; trip_name: string }[];
  warnings: { kind: string; count: number }[];
  shared_expenses_kept: number;
};

const clear: State = {
  status: "active",
  requested_at: null,
  recent_sign_in: true,
  can_request: true,
  blockers: [],
  warnings: [],
  shared_expenses_kept: 0,
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

function serve(state: State, post?: () => Response) {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST" && post) return post();
    return json(state);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function show() {
  render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <PrivacyView />
    </NextIntlClientProvider>,
  );
}

const deleteButton = () => screen.findByRole("button", { name: "Delete my account" });

beforeEach(() => {
  refresh.mockClear();
  document.cookie = "fp_csrf=token";
});
afterEach(() => vi.unstubAllGlobals());

describe("PrivacyView", () => {
  it("keeps delete disabled until the email matches and retention is acknowledged", async () => {
    serve(clear);
    show();
    const button = await deleteButton();
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "wrong@example.com" } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "SAM@example.com" } });
    expect(button).toBeEnabled();
  });

  it("sends the typed confirmation and the acknowledgement", async () => {
    const fetchMock = serve(clear, () =>
      json({ ...clear, status: "deletion_pending", requested_at: "2026-11-09T10:00:00Z" }),
    );
    show();
    const button = await deleteButton();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "sam@example.com" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(button);
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      confirm_email: "sam@example.com",
      acknowledge_retention: true,
    });
  });

  it("explains what's deleted and what stays", async () => {
    serve({ ...clear, shared_expenses_kept: 2 });
    show();
    expect(await screen.findByText("What gets deleted")).toBeInTheDocument();
    expect(screen.getByText("What stays")).toBeInTheDocument();
    expect(screen.getByText(/2 shared trip expenses/)).toBeInTheDocument();
    expect(screen.getByText(/can't be made fully anonymous/)).toBeInTheDocument();
  });

  it("lists what blocks deletion, offers no delete button, and mentions the other route", async () => {
    serve({
      ...clear,
      can_request: false,
      blockers: [{ kind: "trip_balance", trip_id: "t1", trip_name: "Lisbon" }],
    });
    show();
    expect(await screen.findByRole("link", { name: "Lisbon" })).toHaveAttribute(
      "href",
      "/app/trips/t1",
    );
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();
    expect(screen.getByText(/ask the operator to erase/)).toBeInTheDocument();
  });

  it("asks for a fresh sign-in, and comes back to this page", async () => {
    serve({ ...clear, recent_sign_in: false });
    show();
    const link = await screen.findByRole("link", { name: "Sign in again" });
    expect(link.getAttribute("href")).toContain("return_to=%2Fen%2Fapp%2Fsettings%2Fprivacy");
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();
  });

  it("offers a way back while a deletion is pending", async () => {
    serve({ ...clear, status: "deletion_pending", requested_at: "2026-11-09T10:00:00Z" });
    show();
    expect(await screen.findByRole("button", { name: "Keep my account" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();
  });

  it("explains a refusal from the API", async () => {
    serve(clear, () => json({ code: "recent_sign_in_required" }, 403));
    show();
    const button = await deleteButton();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "sam@example.com" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(button);
    expect(await screen.findByText(/sign in with Google again, then confirm/)).toBeInTheDocument();
  });
});

describe("privacy messages", () => {
  const keys = (value: unknown, prefix = ""): string[] =>
    value && typeof value === "object"
      ? Object.entries(value).flatMap(([k, v]) => keys(v, `${prefix}${k}.`))
      : [prefix];

  it.each([
    ["ar", ar],
    ["tr", tr],
  ])("%s has every message English has", (_name, messages) => {
    expect(keys(messages.app.privacy).sort()).toEqual(keys(en.app.privacy).sort());
  });
});
