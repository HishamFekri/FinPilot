"use client";

import { useEffect, useState } from "react";
import { useAuth } from "./auth-provider";
import { useFinanceRevision } from "./finance/finance-provider";
import { recall, remember } from "./load-cache";

type Loaded<T> = { for: string; data?: T; error?: unknown };

/**
 * Loads `load()` for `key`, again whenever the person records something (the finance revision) or
 * asks to retry. A sign-out found here leaves through the auth provider; anything else is returned
 * as `error`, for the page to say so. Old answers never show under a new key.
 */
export function useLoad<T>(key: string, load: () => Promise<T>) {
  const { handleAuthError } = useAuth();
  // Works on every page, inside or outside a ready <FinanceGate>.
  const revision = useFinanceRevision();
  const [attempt, setAttempt] = useState(0);
  const stamp = `${key}|${revision}|${attempt}`;
  const remembered = `${key}|${revision}`;
  const [state, setState] = useState<Loaded<T>>({ for: "" });
  useEffect(() => {
    let cancelled = false;
    load()
      .then((data) => {
        if (cancelled) return;
        remember(remembered, data);
        setState({ for: stamp, data });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setState({ for: stamp, error });
      });
    return () => {
      cancelled = true;
    };
    // `load` is a fresh closure every render; `stamp` says when to run it again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp, handleAuthError]);
  const current = state.for === stamp ? state : null;
  return {
    data: current?.data ?? (state.for.startsWith(`${key}|`) ? state.data : recall<T>(remembered)),
    error: current?.error,
    loading: current === null,
    reload: () => setAttempt((n) => n + 1),
  };
}
