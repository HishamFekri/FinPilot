"use client";

import { useTranslations } from "next-intl";
import { BrandMark as Mark, GoogleG } from "@/components/brand";
import { cn } from "@/lib/utils";
import { useGoogleSignIn } from "./google-sign-in";
import s from "./landing.module.css";

/**
 * "Continue with Google", following Google's sign-in branding: the standard multicolour G, Roboto
 * Medium, Google's light outline style (and its dark style in dark mode). It's a link to the real
 * OAuth start route; `useGoogleSignIn` decides what a click does.
 */
export function GoogleButton({ size = "large" }: { size?: "large" | "compact" }) {
  const t = useTranslations("landing");
  const { href, loading, start } = useGoogleSignIn();

  return (
    <a
      className={cn(s.google, size === "compact" && s.googleCompact)}
      href={href}
      aria-busy={loading || undefined}
      aria-disabled={loading || undefined}
      data-loading={loading || undefined}
      onClick={start}
    >
      <span className={s.googleIcon}>
        {loading ? (
          <span className={s.googleSpinner} aria-hidden="true" />
        ) : (
          <GoogleG className={s.gMark} />
        )}
      </span>
      <span className={s.googleLabel}>{t("google")}</span>
      {loading ? (
        <span className={s.srOnly} role="status">
          {t("googleLoading")}
        </span>
      ) : null}
    </a>
  );
}

/** FinPilot's mark, sized for the landing page. */
export function BrandMark() {
  return <Mark className={s.brandMark} />;
}
