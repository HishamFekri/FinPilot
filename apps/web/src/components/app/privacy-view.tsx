"use client";

import { Download, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { api, API_BASE, ApiError } from "@/lib/api";
import { googleStartHref } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useAuth, useUser } from "./auth-provider";
import s from "./app.module.css";
import { bdi, Button, buttonClass, Loading, Notice } from "./ui";

type Blocker = {
  kind: "trip_balance" | "trip_pending" | "trip_dispute" | "trip_owner";
  trip_id: string;
  trip_name: string;
};
type DeletionState = {
  status: string;
  requested_at: string | null;
  recent_sign_in: boolean;
  can_request: boolean;
  blockers: Blocker[];
  warnings: { kind: "personal_debts" | "installment_plans"; count: number }[];
  shared_expenses_kept: number;
};
type Load = { kind: "loading" } | { kind: "ready"; state: DeletionState } | { kind: "failed" };

const KNOWN_FAILURES = [
  "confirmation_mismatch",
  "recent_sign_in_required",
  "deletion_blocked",
  "deletion_already_requested",
  "deletion_not_requested",
] as const;
type Failure = (typeof KNOWN_FAILURES)[number] | "rate_limited" | "server";

function failureKey(error: unknown): Failure {
  if (error instanceof ApiError) {
    const known = KNOWN_FAILURES.find((code) => code === error.code);
    if (known) return known;
    if (error.kind === "rate_limited") return "rate_limited";
  }
  return "server";
}

/** The person's own data: a download, and asking to delete the account. */
export function PrivacyView() {
  const t = useTranslations("app.privacy");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError, refresh } = useAuth();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState<"download" | "delete" | "cancel" | null>(null);
  const [downloadNote, setDownloadNote] = useState<{ tone: "success" | "error"; text: string }>();
  const [failure, setFailure] = useState<Failure | null>(null);
  const [email, setEmail] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);

  const read = useCallback(async (): Promise<Load | null> => {
    try {
      return { kind: "ready", state: await api<DeletionState>("/me/deletion-request") };
    } catch (error) {
      return handleAuthError(error) ? null : { kind: "failed" };
    }
  }, [handleAuthError]);

  useEffect(() => {
    let cancelled = false;
    void read().then((next) => {
      if (!cancelled && next) setLoad(next);
    });
    return () => {
      cancelled = true;
    };
  }, [read]);

  const reload = async () => {
    const next = await read();
    if (next) setLoad(next);
  };

  const download = async () => {
    setBusy("download");
    setDownloadNote(undefined);
    try {
      const response = await fetch(`${API_BASE}/me/data-export`, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        const text = response.status === 429 ? t("download.rateLimited") : t("download.failed");
        setDownloadNote({ tone: "error", text });
        return;
      }
      const complete = response.headers.get("X-Export-Complete") !== "false";
      const name = /filename="?([^";]+)"?/.exec(response.headers.get("Content-Disposition") ?? "");
      const href = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = href;
      link.download = name?.[1] ?? "finpilot-data.json";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
      setDownloadNote({
        tone: "success",
        text: t(complete ? "download.done" : "download.partial"),
      });
    } catch {
      setDownloadNote({ tone: "error", text: t("download.failed") });
    } finally {
      setBusy(null);
    }
  };

  const act = async (action: "delete" | "cancel") => {
    setBusy(action);
    setFailure(null);
    try {
      if (action === "delete") {
        await api("/me/deletion-request", {
          method: "POST",
          body: { confirm_email: email, acknowledge_retention: true },
        });
      } else {
        await api("/me/deletion-request", { method: "DELETE" });
      }
      setEmail("");
      setAcknowledged(false);
      await refresh();
    } catch (error) {
      if (handleAuthError(error)) return;
      setFailure(failureKey(error));
    } finally {
      setBusy(null);
      await reload();
    }
  };

  const state = load.kind === "ready" ? load.state : null;
  const pending = state?.status === "deletion_pending";
  const matches = email.trim().toLowerCase() === (user.email ?? "").toLowerCase();
  const signInAgain = googleStartHref(locale, `/${locale}/app/settings/privacy`);

  return (
    <div className={`${s.stack} ${s.narrow}`}>
      <header className={s.pageHead}>
        <h1 className={s.pageTitle}>{t("title")}</h1>
        <p className={s.pageLead}>{t("lead")}</p>
      </header>

      <section className={s.panel} aria-labelledby="privacy-download">
        <div className={s.panelHead}>
          <h2 id="privacy-download" className={s.panelTitle}>
            {t("download.title")}
          </h2>
        </div>
        <div className={s.panelBody}>
          <p>{t("download.body")}</p>
          <p className={`${s.small} ${s.muted}`}>{t("download.others")}</p>
          {downloadNote ? <Notice tone={downloadNote.tone}>{downloadNote.text}</Notice> : null}
        </div>
        <div className={s.panelFoot}>
          <Button busy={busy === "download"} onClick={() => void download()}>
            <Download aria-hidden="true" />
            {t("download.action")}
          </Button>
        </div>
      </section>

      <section
        className={s.panel}
        aria-labelledby="privacy-delete"
        aria-busy={load.kind === "loading"}
      >
        <div className={s.panelHead}>
          <h2 id="privacy-delete" className={s.panelTitle}>
            {t("delete.title")}
          </h2>
        </div>
        <div className={s.panelBody}>
          {failure ? <Notice tone="error">{t(`failed.${failure}`)}</Notice> : null}
          {load.kind === "loading" ? (
            <Loading label={t("loading")} />
          ) : load.kind === "failed" ? (
            <Notice tone="error">{t("loadFailed")}</Notice>
          ) : pending ? (
            <>
              <Notice tone="info">
                {t.rich("pending.body", {
                  date: formatDateTime(state?.requested_at ?? "", locale, user.time_zone),
                  bdi,
                })}
              </Notice>
              <p>{t("pending.readOnly")}</p>
              <Button busy={busy === "cancel"} onClick={() => void act("cancel")}>
                {t("pending.cancel")}
              </Button>
            </>
          ) : (
            <>
              <p>{t("delete.body")}</p>
              <h3 className={s.privacyHeading}>{t("delete.removedTitle")}</h3>
              <ul className={s.privacyList}>
                <li>{t("delete.removedFinance")}</li>
                <li>{t("delete.removedProfile")}</li>
              </ul>
              <h3 className={s.privacyHeading}>{t("delete.keptTitle")}</h3>
              <ul className={s.privacyList}>
                <li>
                  {t("delete.keptTrips", {
                    count: state?.shared_expenses_kept ?? 0,
                  })}
                </li>
                <li>{t("delete.keptLabel")}</li>
                <li>{t("delete.keptBackups")}</li>
              </ul>

              {state && state.blockers.length > 0 ? (
                <>
                  <Notice tone="info">{t("blocked.title")}</Notice>
                  <ul className={s.privacyList}>
                    {state.blockers.map((b) => (
                      <li key={`${b.kind}-${b.trip_id}`}>
                        <Link href={`/app/trips/${b.trip_id}`}>
                          <bdi>{b.trip_name}</bdi>
                        </Link>
                        {": "}
                        {t(`blocked.${b.kind}`)}
                      </li>
                    ))}
                  </ul>
                  <p className={`${s.small} ${s.muted}`}>{t("blocked.otherRoute")}</p>
                </>
              ) : (
                <>
                  {state?.warnings.map((w) => (
                    <Notice key={w.kind} tone="info">
                      {t(`warnings.${w.kind}`, { count: w.count })}
                    </Notice>
                  ))}
                  {state?.recent_sign_in ? (
                    <>
                      <label className={s.field}>
                        <span className={s.label}>
                          {t.rich("delete.typeEmail", {
                            email: user.email ?? "",
                            bdi,
                          })}
                        </span>
                        <input
                          className={s.input}
                          dir="ltr"
                          type="email"
                          autoComplete="off"
                          value={email}
                          onChange={(e) => setEmail(e.target.value)}
                        />
                      </label>
                      <label className={s.privacyCheck}>
                        <input
                          type="checkbox"
                          checked={acknowledged}
                          onChange={(e) => setAcknowledged(e.target.checked)}
                        />
                        <span>{t("delete.acknowledge")}</span>
                      </label>
                      <Button
                        variant="danger"
                        busy={busy === "delete"}
                        disabled={!matches || !acknowledged}
                        onClick={() => void act("delete")}
                      >
                        <Trash2 aria-hidden="true" />
                        {t("delete.action")}
                      </Button>
                    </>
                  ) : (
                    <>
                      <Notice tone="info">{t("delete.signInFirst")}</Notice>
                      <a href={signInAgain} className={buttonClass("secondary")}>
                        {t("delete.signInAgain")}
                      </a>
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

/** Shown on every page while a deletion is pending, so nobody thinks the app is simply broken. */
export function DeletionPendingBanner() {
  const t = useTranslations("app.privacy.pending");
  const user = useUser();
  if (user.status !== "deletion_pending") return null;
  return (
    <Notice tone="info" className={s.pageNotice}>
      {t("banner")} <Link href="/app/settings/privacy">{t("bannerLink")}</Link>
    </Notice>
  );
}
