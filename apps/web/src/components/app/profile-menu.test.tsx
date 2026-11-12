import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import tr from "../../../messages/tr.json";
import type { Me } from "@/lib/auth";
import { AuthProvider, useAuth } from "./auth-provider";
import { UserMenu } from "./menus";
import { Avatar } from "./ui";

const MESSAGES = { en, ar, tr } as const;
type Locale = keyof typeof MESSAGES;

const LONG = "Hisham Abdulrahman Mohammed Jouda Al-Khatib Al-Hashimi";
const PICTURE = "https://lh3.googleusercontent.com/a/example=s96-c";

function me(overrides: Partial<Me> = {}): Me {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    email: "hisham@example.com",
    display_name: "Hisham Jouda",
    avatar_url: null,
    locale: "en",
    preferred_currency: "EUR",
    time_zone: "Europe/Istanbul",
    status: "active",
    created_at: "2026-11-05T09:30:00Z",
    ...overrides,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

/** The API: /auth/me answers with `user`; logout answers with `logout` (or fails). */
function api(user: Me, logout: () => Promise<Response>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/v1/auth/logout" && init?.method === "POST") return logout();
    if (url === "/api/v1/auth/me") return json(user);
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const replace = vi.fn();
const navigate = vi.fn();
const signOutError = vi.fn();

function Ready({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  return status === "signed-in" ? children : <p>{status}</p>;
}

function Menu() {
  const root = useRef<HTMLDivElement>(null);
  return (
    <div ref={root}>
      <UserMenu
        container={root}
        placement="sidebar"
        onNavigate={navigate}
        onSignOutError={signOutError}
      />
    </div>
  );
}

function show(locale: Locale = "en") {
  return render(
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]}>
      <AuthProvider>
        <Ready>
          <Menu />
        </Ready>
      </AuthProvider>
    </NextIntlClientProvider>,
  );
}

async function openMenu(name: RegExp | string) {
  const trigger = await screen.findByRole("button", { name });
  fireEvent.click(trigger);
  return screen.findByRole("menu");
}

beforeEach(() => {
  vi.stubGlobal("location", { ...window.location, replace, pathname: "/en/app" });
  document.cookie = "fp_csrf=csrf-token-for-tests";
});
afterEach(() => {
  replace.mockReset();
  navigate.mockReset();
  signOutError.mockReset();
  document.cookie = "fp_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("profile menu", () => {
  it("shows the signed-in person's name, with a dropdown indicator", async () => {
    api(me(), async () => json(null, 204));
    show();
    const trigger = await screen.findByRole("button", { name: "Account menu: Hisham Jouda" });
    expect(trigger).toHaveTextContent("Hisham Jouda");
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger.querySelector("svg")).not.toBeNull();
  });

  it("uses the actual user's name, never a fixed one", async () => {
    api(me({ display_name: "Zeynep Kaya", email: "zeynep@example.com" }), async () =>
      json(null, 204),
    );
    show();
    expect(await screen.findByRole("button", { name: /Zeynep Kaya/ })).toBeInTheDocument();
    expect(screen.queryByText("Hisham Jouda")).toBeNull();
  });

  it("offers profile, settings, devices, language, appearance and sign out", async () => {
    api(me(), async () => json(null, 204));
    show();
    const menu = await openMenu(/Hisham Jouda/);
    const items = [...menu.querySelectorAll('[role^="menuitem"]')].map((item) =>
      item.textContent?.trim(),
    );
    expect(items).toEqual([
      "My profile",
      "Settings",
      "Signed-in devices",
      expect.stringMatching(/^Language/),
      expect.stringMatching(/^Appearance/),
      "Sign out",
    ]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Settings" }));
    expect(navigate).toHaveBeenCalledWith("/settings");
  });

  it("goes to the profile and to signed-in devices", async () => {
    api(me(), async () => json(null, 204));
    show();
    fireEvent.click(
      within(await openMenu(/Hisham Jouda/)).getByRole("menuitem", { name: "My profile" }),
    );
    expect(navigate).toHaveBeenLastCalledWith("/settings#profile");
    fireEvent.click(
      within(await openMenu(/Hisham Jouda/)).getByRole("menuitem", { name: "Signed-in devices" }),
    );
    expect(navigate).toHaveBeenLastCalledWith("/settings/sessions");
  });

  it("keeps a long name whole for assistive technology, and shows it in full in the menu", async () => {
    api(me({ display_name: LONG }), async () => json(null, 204));
    show();
    const trigger = await screen.findByRole("button", { name: `Account menu: ${LONG}` });
    expect(trigger).toHaveTextContent(LONG);
    const menu = await openMenu(new RegExp(LONG));
    expect(within(menu).getByText(LONG)).toBeInTheDocument();
  });
});

describe("avatar", () => {
  it("shows initials when there's no picture", async () => {
    api(me(), async () => json(null, 204));
    show();
    const trigger = await screen.findByRole("button", { name: /Hisham Jouda/ });
    expect(trigger.querySelector("img")).toBeNull();
    expect(trigger.querySelector("[data-avatar]")).toHaveTextContent("HJ");
  });

  it("shows the Google picture without a referrer, then initials if it fails to load", () => {
    render(<Avatar name="Hisham Jouda" email={null} src={PICTURE} />);
    const img = document.querySelector("img") as HTMLImageElement;
    expect(img).toHaveAttribute("src", PICTURE);
    expect(img).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(img).toHaveAttribute("alt", "");
    fireEvent.error(img);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("[data-avatar]")).toHaveTextContent("HJ");
  });

  it("gives a new picture its own chance after an old one failed", () => {
    const { rerender } = render(<Avatar name="Hisham Jouda" email={null} src={PICTURE} />);
    fireEvent.error(document.querySelector("img") as HTMLImageElement);
    rerender(<Avatar name="Hisham Jouda" email={null} src={`${PICTURE}&v=2`} />);
    expect(document.querySelector("img")).toHaveAttribute("src", `${PICTURE}&v=2`);
  });
});

describe("signing out", () => {
  it.each<[Locale, string]>([
    ["en", "/en"],
    ["ar", "/ar"],
    ["tr", "/tr"],
  ])(
    "revokes the session, then replaces the page with the %s landing page",
    async (locale, home) => {
      const fetchMock = api(me({ locale }), async () => new Response(null, { status: 204 }));
      show(locale);
      const signOut = MESSAGES[locale].app.user.signOut;
      const menu = await openMenu(/Hisham Jouda/);
      fireEvent.click(within(menu).getByRole("menuitem", { name: signOut }));

      await waitFor(() => expect(replace).toHaveBeenCalledWith(home));
      // replace(), not assign(): Back can't return to the signed-in page.
      const logout = fetchMock.mock.calls.find(([url]) => url === "/api/v1/auth/logout");
      expect(logout?.[1]?.method).toBe("POST");
      expect(new Headers(logout?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-token-for-tests");
      // Nothing private stays on screen while the browser leaves.
      expect(screen.queryByRole("button", { name: /Hisham Jouda/ })).toBeNull();
      expect(signOutError).not.toHaveBeenCalled();
    },
  );

  it("stays signed in and says so when the server can't be reached", async () => {
    api(me(), async () => {
      throw new TypeError("Failed to fetch");
    });
    show();
    const menu = await openMenu(/Hisham Jouda/);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign out" }));

    await waitFor(() => expect(signOutError).toHaveBeenCalledTimes(1));
    expect(replace).not.toHaveBeenCalled();
    // Still signed in, and the item can be tried again.
    expect(screen.getByRole("button", { name: /Hisham Jouda/ })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Sign out" })).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("stays signed in when the server refuses the sign-out", async () => {
    api(me(), async () => json({ error: { code: "internal_error" } }, 500));
    show();
    fireEvent.click(
      within(await openMenu(/Hisham Jouda/)).getByRole("menuitem", { name: "Sign out" }),
    );
    await waitFor(() => expect(signOutError).toHaveBeenCalled());
    expect(replace).not.toHaveBeenCalled();
  });

  it("treats an already-ended session as signed out", async () => {
    api(me(), async () => json({ error: { code: "unauthenticated" } }, 401));
    show();
    fireEvent.click(
      within(await openMenu(/Hisham Jouda/)).getByRole("menuitem", { name: "Sign out" }),
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/en"));
  });
});
