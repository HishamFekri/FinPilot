"use client";

import { Map as MapIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError } from "@/lib/api";
import { INVITE_STORAGE_KEY, type InvitePreview, tripsApi } from "@/lib/trips";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { Button, Notice, Spinner } from "../ui";
import { useDateRange } from "./parts";
import s from "./trips.module.css";

function readToken(): string | null {
  try {
    return window.sessionStorage.getItem(INVITE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function forget() {
  try {
    window.sessionStorage.removeItem(INVITE_STORAGE_KEY);
  } catch {
    // Nothing to clear.
  }
}

/**
 * Joining a trip from an invitation link. The token arrives in the link's fragment (never sent
 * to a server or written to logs), is kept in this tab while the person signs in, and is sent
 * to the API in a request body (API_SPEC.md §9).
 */
export function JoinView() {
  const t = useTranslations("app.trips.join");
  const router = useRouter();
  const range = useDateRange();
  const { handleAuthError } = useAuth();
  const [token] = useState(() => (typeof window === "undefined" ? null : readToken()));
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; preview: InvitePreview }
    | { kind: "invalid"; reason: "missing" | "used" | "expired" | "invalid" | "failed" }
  >(() => (token ? { kind: "loading" } : { kind: "invalid", reason: "missing" }));
  const [busy, setBusy] = useState<"accept" | "decline" | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    tripsApi
      .previewInvite(token)
      .then((preview) => {
        if (!cancelled) setState({ kind: "ready", preview });
      })
      .catch((error: unknown) => {
        if (cancelled || handleAuthError(error)) return;
        const code = error instanceof ApiError ? error.code : null;
        setState({
          kind: "invalid",
          reason:
            code === "invitation_used"
              ? "used"
              : code === "invitation_expired"
                ? "expired"
                : code === "invitation_invalid"
                  ? "invalid"
                  : "failed",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [token, handleAuthError]);

  const accept = async () => {
    if (!token) return;
    setBusy("accept");
    setFailure(null);
    try {
      const trip = await tripsApi.accept(token);
      forget();
      router.replace(`/app/trips/${trip.id}`);
    } catch (error) {
      if (handleAuthError(error)) return;
      const code = error instanceof ApiError ? error.code : null;
      setFailure(code === "already_member" ? t("already") : t("failed"));
      setBusy(null);
    }
  };
  const decline = async () => {
    if (!token) return;
    setBusy("decline");
    try {
      await tripsApi.decline(token);
    } catch {
      // Declining a link that no longer works is the same outcome.
    }
    forget();
    router.replace("/app/trips");
  };

  return (
    <div className={s.page}>
      <h1 className={appStyles.pageTitle}>{t("title")}</h1>
      {state.kind === "loading" ? (
        <p className={appStyles.muted} role="status">
          <Spinner /> <span>{t("loading")}</span>
        </p>
      ) : state.kind === "invalid" ? (
        <>
          <Notice tone="error">{t(`invalid.${state.reason}`)}</Notice>
          <p>
            <Link href="/app/trips" className={appStyles.linkBtn}>
              {t("toTrips")}
            </Link>
          </p>
        </>
      ) : (
        <section className={s.card} aria-labelledby="join-title">
          <div className={s.cardHead}>
            <h2 id="join-title" className={s.cardTitle}>
              <MapIcon aria-hidden="true" style={{ inlineSize: 18, blockSize: 18 }} />{" "}
              <span dir="auto">{state.preview.trip_name}</span>
            </h2>
          </div>
          <div className={s.cardBody}>
            <p className={s.meta}>
              {state.preview.destination ? (
                <span dir="auto">{state.preview.destination}</span>
              ) : null}
              {range(state.preview.start_date, state.preview.end_date) ? (
                <span>{range(state.preview.start_date, state.preview.end_date)}</span>
              ) : null}
              <span>
                <bdi dir="ltr">{state.preview.base_currency}</bdi>
              </span>
            </p>
            <p>
              {t.rich("invitedBy", {
                name: state.preview.inviter,
                b: (c) => <strong dir="auto">{c}</strong>,
              })}
            </p>
            <p className={s.notice}>{t("privacy")}</p>
            {failure ? <Notice tone="error">{failure}</Notice> : null}
            {state.preview.already_member ? (
              <Notice>{t("already")}</Notice>
            ) : (
              <div className={s.actions}>
                <Button variant="primary" busy={busy === "accept"} onClick={accept}>
                  {t("accept")}
                </Button>
                <Button variant="ghost" busy={busy === "decline"} onClick={decline}>
                  {t("decline")}
                </Button>
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
