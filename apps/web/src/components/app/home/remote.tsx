"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { Notice } from "../ui";

export type Remote<T> = { kind: "loading" } | { kind: "failed" } | { kind: "ready"; data: T };

/**
 * Fetches when `key` changes (or on retry). The latest data stays on screen while a refetch runs,
 * so totals don't flicker after each save.
 */
export function useRemote<T>(key: string, fetcher: () => Promise<T>): [Remote<T>, () => void] {
  const { handleAuthError } = useAuth();
  const [state, setState] = useState<Remote<T>>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const latest = useRef(fetcher);
  useEffect(() => {
    latest.current = fetcher;
  });
  useEffect(() => {
    let cancelled = false;
    latest
      .current()
      .then((data) => {
        if (!cancelled) setState({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setState({ kind: "failed" });
      });
    return () => {
      cancelled = true;
    };
  }, [key, attempt, handleAuthError]);
  return [state, () => setAttempt((n) => n + 1)];
}

export function Retry({ onRetry }: { onRetry: () => void }) {
  const t = useTranslations("app.home");
  return (
    <Notice tone="error">
      {t("loadFailed")}{" "}
      <button type="button" className={appStyles.linkBtn} onClick={onRetry}>
        {t("retry")}
      </button>
    </Notice>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className={appStyles.skeleton} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className={appStyles.skel} />
      ))}
    </div>
  );
}
