import { type Locale, routing } from "@/i18n/routing";

/** The signed-in user, exactly as `GET /auth/me` returns it. Nothing here is invented. */
export type Me = {
  id: string;
  email: string | null;
  display_name: string | null;
  /** The Google profile picture from the last sign-in, or null. A URL on Google's image host
   *  that may stop working at any time, so the app always has initials to fall back on. */
  avatar_url: string | null;
  locale: Locale;
  preferred_currency: string | null;
  time_zone: string;
  status: string;
  created_at: string;
};

/** The fields `PATCH /auth/me` accepts. Email and the Google identity can't be changed. */
export type ProfileChanges = Partial<
  Pick<Me, "display_name" | "locale" | "preferred_currency" | "time_zone">
>;

/** One row of `GET /auth/sessions`. */
export type SessionInfo = {
  id: string;
  current: boolean;
  device_label: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
};

/** Present only when the browser holds a session cookie. Its value is never read here. */
export const SESSION_COOKIES = ["__Host-fp_session", "fp_session"] as const;

/** The codes the API's Google routes can send back (backend `auth.py`, `ERROR_CODES`). */
export const SIGN_IN_ERRORS = [
  "cancelled",
  "email_not_verified",
  "account_unavailable",
  "google_unavailable",
  "failed",
  "unreachable",
] as const;
export type SignInError = (typeof SIGN_IN_ERRORS)[number];

/** Notices the web app itself sends to the landing page. */
export const SIGN_IN_NOTICES = ["expired"] as const;
export type SignInNotice = (typeof SIGN_IN_NOTICES)[number];

export function signInError(value: string | null | undefined): SignInError | null {
  if (!value) return null;
  // Anything unrecognised is shown as the generic failure, never echoed.
  return SIGN_IN_ERRORS.find((code) => code === value) ?? "failed";
}

export function signInNotice(value: string | null | undefined): SignInNotice | null {
  return SIGN_IN_NOTICES.find((code) => code === value) ?? null;
}

export const appHome = (locale: Locale) => `/${locale}/app`;

// Only paths inside the app may be returned to, and only in the shape the API accepts for
// `return_to` (backend `google_signin.py`): letters, digits, and `._~/-`. No query, no fragment.
const APP_PATH = /^\/(en|ar|tr)\/app(\/[A-Za-z0-9._~-]+)*\/?$/;

/**
 * A safe place to land after signing in, or null. Rejects anything that isn't a plain path inside
 * the app: other origins, protocol-relative `//host`, backslashes, encoded characters, and `..`.
 */
export function safeReturnPath(value: string | null | undefined): string | null {
  if (!value || value.length > 200) return null;
  if (!APP_PATH.test(value)) return null;
  if (value.split("/").some((segment) => segment === "." || segment === "..")) return null;
  return value;
}

/**
 * The landing page, which is where everyone signs in, remembering where they were going and why
 * they're there. Home is the default destination, so it isn't spelled out.
 */
export function landingHref(
  locale: Locale,
  options: { next?: string | null; notice?: SignInNotice | null; error?: SignInError | null } = {},
) {
  const params = new URLSearchParams();
  const next = safeReturnPath(options.next);
  if (next && next !== appHome(locale)) params.set("next", next);
  if (options.error) params.set("error", options.error);
  else if (options.notice) params.set("notice", options.notice);
  const query = params.toString();
  return `/${locale}${query ? `?${query}` : ""}`;
}

/** The real Google sign-in: the backend's start route, through the web app's /api rewrite. */
export function googleStartHref(locale: Locale, next?: string | null): string {
  const params = new URLSearchParams({ locale });
  const returnTo = safeReturnPath(next) ?? appHome(locale);
  // Land in the language the person is signing in with, at the same place in the app.
  params.set("return_to", returnTo.replace(/^\/(en|ar|tr)\//, `/${locale}/`));
  return `/api/v1/auth/google/start?${params.toString()}`;
}

export function isLocale(value: string): value is Locale {
  return (routing.locales as readonly string[]).includes(value);
}

/** Initials for the avatar fallback. The API returns no picture, so none is invented. */
export function initials(name: string | null | undefined, email: string | null | undefined) {
  const source = (name ?? "").trim() || (email ?? "").split("@")[0] || "";
  const words = source.split(/[\s._-]+/u).filter(Boolean);
  const letters =
    words.length >= 2
      ? [words[0] ?? "", words[words.length - 1] ?? ""].map((w) => Array.from(w)[0] ?? "")
      : Array.from(words[0] ?? "").slice(0, 2);
  return letters.join("").toLocaleUpperCase() || "?";
}
