"use client";

import { CircleAlert, Info, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { signInError, signInNotice } from "@/lib/auth";
import {
  clearAuthMessage,
  showAuthMessage,
  useAuthMessage,
  useGoogleSignIn,
} from "./google-sign-in";
import s from "./landing.module.css";

/**
 * Why someone is back on the landing page: a failed sign-in, or a session that ended. Fixed to the
 * viewport so it's seen from any of the page's Google buttons, however far down.
 *
 * The URL's values are checked against fixed lists, so nothing from it is shown as text, and they
 * are removed from the address bar so a reload or a shared link doesn't repeat the message. `next`
 * stays: it says where to go after signing in.
 */
export function AuthMessage() {
  const t = useTranslations("signIn");
  const message = useAuthMessage();
  const { href, loading, start } = useGoogleSignIn();

  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("error") && !url.searchParams.has("notice")) return;
    const error = signInError(url.searchParams.get("error"));
    const notice = signInNotice(url.searchParams.get("notice"));
    url.searchParams.delete("error");
    url.searchParams.delete("notice");
    window.history.replaceState(window.history.state, "", url.href);
    if (error) showAuthMessage({ kind: "error", code: error });
    else if (notice) showAuthMessage({ kind: "notice", code: notice });
  }, []);

  if (!message) return null;
  const isError = message.kind === "error";

  return (
    <div
      key={message.id}
      className={s.authMessage}
      data-tone={message.kind}
      role={isError ? "alert" : "status"}
    >
      {isError ? (
        <CircleAlert className={s.authIcon} aria-hidden="true" />
      ) : (
        <Info className={s.authIcon} aria-hidden="true" />
      )}
      <p className={s.authText}>
        {isError ? t(`errors.${message.code}`) : t(`notices.${message.code}`)}
      </p>
      <a
        className={s.authAction}
        href={href}
        aria-busy={loading || undefined}
        aria-disabled={loading || undefined}
        onClick={start}
      >
        {isError ? t("retry") : t("signInAgain")}
      </a>
      <button
        type="button"
        className={s.authDismiss}
        aria-label={t("dismiss")}
        onClick={clearAuthMessage}
      >
        <X aria-hidden="true" />
      </button>
    </div>
  );
}
