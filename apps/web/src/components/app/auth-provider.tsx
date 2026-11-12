"use client";

import { useLocale } from "next-intl";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { Locale } from "@/i18n/routing";
import { api, ApiError } from "@/lib/api";
import { landingHref, type Me } from "@/lib/auth";

/**
 * Who is signed in, as far as the API says. Nothing about the user is stored in the browser:
 * the session cookie is HttpOnly, and the profile lives in memory only for this page.
 *
 * | State          | Meaning                                                           |
 * |----------------|-------------------------------------------------------------------|
 * | checking       | Waiting for `GET /auth/me`. Nothing private is shown              |
 * | signed-in      | The API confirmed the session                                     |
 * | leaving        | Signing out, or being sent to the landing page. Nothing private  |
 * | unavailable    | The API didn't answer. The session may be fine, so nobody is      |
 * |                | signed out; the page offers to try again                         |
 */
export type AuthStatus = "checking" | "signed-in" | "leaving" | "unavailable";

type AuthContextValue = {
  status: AuthStatus;
  user: Me | null;
  /** Ask the API again. */
  refresh: () => Promise<void>;
  /** Replace the profile after a successful update, so nothing is stale. */
  setUser: (user: Me) => void;
  /**
   * Ends the session on the server, then goes to the landing page in the same language. Throws if
   * the API can't be reached: the person is still signed in, and is told so.
   */
  signOut: () => Promise<void>;
  /**
   * Every API failure on an app page goes through here. Confirmed sign-outs leave; anything else
   * returns false, for the page to show its own message.
   */
  handleAuthError: (error: unknown) => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

/** The signed-in user. Only for components rendered behind the gate. */
export function useUser(): Me {
  const { user } = useAuth();
  if (!user) throw new Error("useUser is only available once signed in");
  return user;
}

type MeResult = { ok: true; me: Me } | { ok: false; error: unknown };

async function fetchMe(): Promise<MeResult> {
  try {
    return { ok: true, me: await api<Me>("/auth/me") };
  } catch (error) {
    return { ok: false, error };
  }
}

async function clearCookies() {
  // With no live session, logout just clears the cookies, so the proxy stops treating this
  // browser as signed in. Best effort: the session is already dead on the server.
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {
    /* ignore */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const locale = useLocale() as Locale;
  const [status, setStatus] = useState<AuthStatus>("checking");
  const [user, setUserState] = useState<Me | null>(null);
  const leaving = useRef(false);

  const leave = useCallback(async (target: string, clear: boolean) => {
    if (leaving.current) return;
    leaving.current = true;
    setStatus("leaving");
    setUserState(null);
    if (clear) await clearCookies();
    // replace(), so Back doesn't return to a page that belonged to the old session.
    window.location.replace(target);
  }, []);

  const handleAuthError = useCallback(
    (error: unknown) => {
      if (!(error instanceof ApiError)) return false;
      const here = window.location.pathname;
      if (error.kind === "unauthenticated") {
        void leave(landingHref(locale, { next: here, notice: "expired" }), true);
        return true;
      }
      if (error.kind === "account_unavailable") {
        void leave(landingHref(locale, { error: "account_unavailable" }), true);
        return true;
      }
      return false;
    },
    [leave, locale],
  );

  // Applies the API's answer. Called only once the answer has arrived.
  const apply = useCallback(
    (result: MeResult) => {
      if (leaving.current) return;
      if (result.ok) {
        setUserState(result.me);
        setStatus("signed-in");
        return;
      }
      if (handleAuthError(result.error)) return;
      // Not a confirmed sign-out: keep the session, and say the API is unreachable.
      setStatus("unavailable");
    },
    [handleAuthError],
  );

  const refresh = useCallback(async () => {
    if (leaving.current) return;
    setStatus((current) => (current === "signed-in" ? current : "checking"));
    apply(await fetchMe());
  }, [apply]);

  const signOut = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch (error) {
      // An already-ended session is as good as signed out. Anything else: stay, and report it.
      if (!(error instanceof ApiError) || error.kind !== "unauthenticated") throw error;
    }
    // The API has revoked the session and cleared its cookies. replace(), so Back can't return to
    // the page that was open.
    await leave(`/${locale}`, false);
  }, [leave, locale]);

  useEffect(() => {
    let cancelled = false;
    void fetchMe().then((result) => {
      if (!cancelled) apply(result);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  // A page restored from the back-forward cache shows what it showed before. Hide it and ask the
  // API again, so a signed-out tab can't reveal anything with Back.
  useEffect(() => {
    const onShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      leaving.current = false;
      setUserState(null);
      setStatus("checking");
      void refresh();
    };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, [refresh]);

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, refresh, setUser: setUserState, signOut, handleAuthError }),
    [status, user, refresh, signOut, handleAuthError],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
