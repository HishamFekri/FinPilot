"use client";

import { AlertDialog } from "@base-ui/react/alert-dialog";
import { MonitorSmartphone, RefreshCw } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { api, ApiError } from "@/lib/api";
import type { SessionInfo } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useAuth, useUser } from "./auth-provider";
import s from "./app.module.css";
import { bdi, Button, Loading, Notice, Tag } from "./ui";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; sessions: SessionInfo[] }
  | { kind: "failed"; reason: "unavailable" | "server" };

type ActionError = "unavailable" | "rate_limited" | "server" | null;

function reasonOf(error: unknown): Exclude<ActionError, null> {
  if (error instanceof ApiError && (error.kind === "unavailable" || error.kind === "rate_limited"))
    return error.kind;
  return "server";
}

/**
 * Where this account is signed in, from `GET /auth/sessions`. The device label is the API's own
 * (built from the browser's user agent); nothing else about a device is guessed or shown.
 */
export function SessionsView() {
  const t = useTranslations("app.sessions");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError, signOut } = useAuth();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<ActionError>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  /** The list, or what went wrong; null when the session has ended (the provider takes over). */
  const loadSessions = useCallback(async (): Promise<Load | null> => {
    try {
      return { kind: "ready", sessions: await api<SessionInfo[]>("/auth/sessions") };
    } catch (error) {
      if (handleAuthError(error)) return null;
      return {
        kind: "failed",
        reason:
          error instanceof ApiError && error.kind === "unavailable" ? "unavailable" : "server",
      };
    }
  }, [handleAuthError]);

  const fetchSessions = useCallback(async () => {
    const result = await loadSessions();
    if (result) setLoad(result);
  }, [loadSessions]);

  useEffect(() => {
    let cancelled = false;
    void loadSessions().then((result) => {
      if (!cancelled && result) setLoad(result);
    });
    return () => {
      cancelled = true;
    };
  }, [loadSessions]);

  const revoke = async (session: SessionInfo) => {
    setActionError(null);
    setMessage(null);
    if (session.current) {
      // Ending this session is signing out.
      setPending(session.id);
      try {
        await signOut();
      } catch (error) {
        setActionError(reasonOf(error));
        setPending(null);
      }
      return;
    }
    setPending(session.id);
    try {
      await api(`/auth/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
      setMessage(t("revoked"));
      await fetchSessions();
    } catch (error) {
      if (handleAuthError(error)) return;
      // Already gone (expired, or ended elsewhere): just refresh the list.
      if (error instanceof ApiError && error.kind === "not_found") await fetchSessions();
      else setActionError(reasonOf(error));
    } finally {
      setPending(null);
    }
  };

  const revokeOthers = async () => {
    setActionError(null);
    setMessage(null);
    setPending("others");
    try {
      await api("/auth/sessions", { method: "DELETE" });
      setConfirmOpen(false);
      setMessage(t("revokedOthers"));
      await fetchSessions();
    } catch (error) {
      if (handleAuthError(error)) return;
      setConfirmOpen(false);
      setActionError(reasonOf(error));
    } finally {
      setPending(null);
    }
  };

  const others = load.kind === "ready" ? load.sessions.filter((x) => !x.current).length : 0;
  const when = (iso: string) => formatDateTime(iso, locale, user.time_zone);

  return (
    <div ref={rootRef} className={`${s.stack} ${s.narrow}`}>
      <header className={s.pageHead}>
        <h1 className={s.pageTitle}>{t("title")}</h1>
        <p className={s.pageLead}>{t("lead")}</p>
      </header>

      {message ? <Notice tone="success">{message}</Notice> : null}
      {actionError ? <Notice tone="error">{t(`failed.${actionError}`)}</Notice> : null}

      <section
        className={s.panel}
        aria-labelledby="sessions-title"
        aria-busy={load.kind === "loading"}
      >
        <div className={s.panelHead}>
          <h2 id="sessions-title" className={s.panelTitle}>
            {load.kind === "ready" ? t("count", { count: load.sessions.length }) : t("listTitle")}
          </h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void fetchSessions()}
            disabled={load.kind === "loading"}
          >
            <RefreshCw aria-hidden="true" />
            {t("refresh")}
          </Button>
        </div>

        {load.kind === "loading" ? (
          <div className={s.panelBody}>
            <Loading label={t("loading")} />
          </div>
        ) : load.kind === "failed" ? (
          <div className={s.panelBody}>
            <Notice tone="error">{t(`loadFailed.${load.reason}`)}</Notice>
          </div>
        ) : (
          <ul className={s.sessions}>
            {load.sessions.map((session) => (
              <li key={session.id} className={s.session}>
                <span className={s.sessionIcon}>
                  <MonitorSmartphone aria-hidden="true" />
                </span>
                <span className={s.rowText}>
                  <span className={s.sessionName}>
                    <bdi>{session.device_label || t("unknownDevice")}</bdi>
                    {session.current ? <Tag tone="positive">{t("thisDevice")}</Tag> : null}
                  </span>
                  <span className={s.sessionMeta}>
                    {t.rich("signedIn", { date: when(session.created_at), bdi })}
                  </span>
                  <span className={s.sessionMeta}>
                    {t.rich("lastActive", { date: when(session.last_seen_at), bdi })}
                  </span>
                  <span className={s.sessionMeta}>
                    {t.rich("expires", { date: when(session.expires_at), bdi })}
                  </span>
                </span>
                <Button
                  variant={session.current ? "secondary" : "danger"}
                  size="sm"
                  busy={pending === session.id}
                  disabled={pending !== null && pending !== session.id}
                  onClick={() => void revoke(session)}
                  aria-label={
                    session.current
                      ? t("signOutHere")
                      : t("revokeLabel", { device: session.device_label || t("unknownDevice") })
                  }
                >
                  {session.current ? t("signOutHere") : t("revoke")}
                </Button>
              </li>
            ))}
          </ul>
        )}

        {load.kind === "ready" && others > 0 ? (
          <div className={s.panelFoot}>
            <AlertDialog.Root open={confirmOpen} onOpenChange={setConfirmOpen}>
              <AlertDialog.Trigger
                className={`${s.btn} ${s.btnDanger}`}
                disabled={pending !== null}
              >
                {t("revokeOthers")}
              </AlertDialog.Trigger>
              <AlertDialog.Portal container={rootRef}>
                <AlertDialog.Backdrop className={s.dialogBackdrop} />
                <AlertDialog.Popup className={s.dialog}>
                  <AlertDialog.Title className={s.dialogTitle}>
                    {t("confirmTitle", { count: others })}
                  </AlertDialog.Title>
                  <AlertDialog.Description className={s.dialogBody}>
                    {t("confirmBody")}
                  </AlertDialog.Description>
                  <div className={s.dialogActions}>
                    <AlertDialog.Close className={`${s.btn} ${s.btnSecondary}`}>
                      {t("cancel")}
                    </AlertDialog.Close>
                    <Button
                      variant="danger"
                      busy={pending === "others"}
                      onClick={() => void revokeOthers()}
                    >
                      {t("confirm")}
                    </Button>
                  </div>
                </AlertDialog.Popup>
              </AlertDialog.Portal>
            </AlertDialog.Root>
            <span className={s.status}>{t("othersNote")}</span>
          </div>
        ) : null}
      </section>
    </div>
  );
}
