"use client";

import { useLocale } from "next-intl";
import { type MouseEvent, useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { Locale } from "@/i18n/routing";
import { api, apiReachable, readCsrfToken } from "@/lib/api";
import {
  appHome,
  googleStartHref,
  type Me,
  safeReturnPath,
  type SignInError,
  type SignInNotice,
} from "@/lib/auth";

/**
 * Signing in from the landing page, which is the only way in. The page has several Google buttons
 * and one message, so the message lives here where every button can reach it.
 */
export type AuthMessage = { id: number } & (
  { kind: "error"; code: SignInError } | { kind: "notice"; code: SignInNotice }
);
type NewMessage = { kind: "error"; code: SignInError } | { kind: "notice"; code: SignInNotice };

let current: AuthMessage | null = null;
let lastId = 0;
const listeners = new Set<() => void>();

function publish(next: AuthMessage | null) {
  current = next;
  listeners.forEach((listener) => listener());
}

// A new id each time, so a repeated failure is announced again instead of changing nothing.
export const showAuthMessage = (message: NewMessage) => publish({ ...message, id: ++lastId });
export const clearAuthMessage = () => publish(null);

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useAuthMessage(): AuthMessage | null {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => null,
  );
}

const noSubscription = () => () => {};
const returnPathFromUrl = () =>
  safeReturnPath(new URLSearchParams(window.location.search).get("next"));

/**
 * The link to Google and what happens on click:
 * - someone already signed in (the API confirms it) goes straight into the app, not via Google;
 * - if FinPilot's API can't be reached, the person stays here and is told so, instead of landing
 *   on a raw error page;
 * - otherwise the browser goes on to Google, and comes back to the page they were heading for.
 * After a click it stops accepting clicks, so a second one can't start a second sign-in. Returning
 * with the Back button resets it.
 */
export function useGoogleSignIn() {
  const locale = useLocale() as Locale;
  const next = useSyncExternalStore(noSubscription, returnPathFromUrl, () => null);
  const [loading, setLoading] = useState(false);
  const href = googleStartHref(locale, next);

  useEffect(() => {
    const reset = (e: PageTransitionEvent) => {
      if (e.persisted) setLoading(false);
    };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  const start = useCallback(
    async (event: MouseEvent<HTMLAnchorElement>) => {
      event.preventDefault();
      if (loading) return;
      setLoading(true);
      const go = (path: string) =>
        window.location.assign(new URL(path, window.location.origin).href);
      if (readCsrfToken()) {
        try {
          await api<Me>("/auth/me");
          go(next ?? appHome(locale));
          return;
        } catch {
          // Not signed in after all, or the API is down: decided below.
        }
      }
      if (await apiReachable()) {
        go(href);
      } else {
        setLoading(false);
        showAuthMessage({ kind: "error", code: "unreachable" });
      }
    },
    [href, loading, locale, next],
  );

  return { href, loading, start };
}
