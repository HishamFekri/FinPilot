"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { useRouter } from "@/i18n/navigation";
import { INVITE_STORAGE_KEY } from "@/lib/trips";

/**
 * /{locale}/invite#<token>: keeps the token in this tab (sessionStorage), clears it from the
 * address bar, and continues to the join page, which asks the person to sign in first if needed.
 */
export function InviteRedirect() {
  const t = useTranslations("app.trips.join");
  const router = useRouter();
  useEffect(() => {
    const token = window.location.hash.slice(1);
    try {
      if (token) window.sessionStorage.setItem(INVITE_STORAGE_KEY, token);
    } catch {
      // Without storage the join page explains that the link couldn't be read.
    }
    window.history.replaceState(null, "", window.location.pathname);
    router.replace("/app/trips/join");
  }, [router]);
  return <p style={{ padding: 24 }}>{t("loading")}</p>;
}
