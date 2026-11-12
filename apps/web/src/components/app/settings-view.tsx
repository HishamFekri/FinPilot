"use client";

import { ChevronRight, CircleAlert, MonitorSmartphone, ShieldCheck, Tags, Zap } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useId, useMemo, useState } from "react";
import { Link } from "@/i18n/navigation";
import { type Locale, localeNames, routing } from "@/i18n/routing";
import { api, ApiError } from "@/lib/api";
import type { Me, ProfileChanges } from "@/lib/auth";
import { currencyOptions, deviceTimeZone, formatDate, timeZoneOptions } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useAuth, useUser } from "./auth-provider";
import s from "./app.module.css";
import { switchLanguage } from "./menus";
import { bdi, Button, buttonClass, Notice } from "./ui";
import { Select } from "./select";

type Draft = {
  display_name: string;
  locale: Locale;
  preferred_currency: string; // "" means not set
  time_zone: string;
};

type FieldErrors = Partial<Record<keyof Draft, "required" | "tooLong" | "characters" | "invalid">>;

const NAME_MAX = 100;
// Control and formatting characters (bidirectional overrides among them) are refused by the API.
const DISALLOWED = /[\p{Cc}\p{Cf}]/u;

function draftOf(user: Me): Draft {
  return {
    display_name: user.display_name ?? "",
    locale: user.locale,
    preferred_currency: user.preferred_currency ?? "",
    time_zone: user.time_zone,
  };
}

/** Client-side checks mirror the API's, for quick feedback. The API still decides. */
export function validateDraft(draft: Draft): FieldErrors {
  const errors: FieldErrors = {};
  const name = draft.display_name.trim();
  if (!name) errors.display_name = "required";
  else if (name.length > NAME_MAX) errors.display_name = "tooLong";
  else if (DISALLOWED.test(name)) errors.display_name = "characters";
  if (draft.preferred_currency && !/^[A-Z]{3}$/.test(draft.preferred_currency))
    errors.preferred_currency = "invalid";
  if (!draft.time_zone) errors.time_zone = "invalid";
  return errors;
}

/** Only what changed, in the API's shape. */
export function changesOf(user: Me, draft: Draft): ProfileChanges {
  const changes: ProfileChanges = {};
  const name = draft.display_name.trim();
  if (name !== (user.display_name ?? "")) changes.display_name = name;
  if (draft.locale !== user.locale) changes.locale = draft.locale;
  const currency = draft.preferred_currency || null;
  if (currency !== user.preferred_currency) changes.preferred_currency = currency;
  if (draft.time_zone !== user.time_zone) changes.time_zone = draft.time_zone;
  return changes;
}

type Status =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "failed"; reason: "unavailable" | "rate_limited" | "invalid" | "server" };

export function SettingsView() {
  const t = useTranslations("app.settings");
  const categories = useTranslations("app.categories");
  const templates = useTranslations("app.templates");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { setUser, handleAuthError } = useAuth();
  const [draft, setDraft] = useState<Draft>(() => draftOf(user));
  const [errors, setErrors] = useState<FieldErrors>({});
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const id = useId();

  const currencies = useMemo(() => currencyOptions(locale), [locale]);
  const zones = useMemo(() => {
    const list = timeZoneOptions();
    return list.includes(draft.time_zone) ? list : [draft.time_zone, ...list];
  }, [draft.time_zone]);
  const device = deviceTimeZone();
  const changes = changesOf(user, draft);
  const dirty = Object.keys(changes).length > 0;

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
    if (status.kind !== "saving") setStatus({ kind: "idle" });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = validateDraft(draft);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      document.getElementById(`${id}-${Object.keys(found)[0]}`)?.focus();
      return;
    }
    if (!dirty) return;
    setStatus({ kind: "saving" });
    try {
      const saved = await api<Me>("/auth/me", { method: "PATCH", body: changes });
      setUser(saved);
      setDraft(draftOf(saved));
      setStatus({ kind: "saved" });
      // The page's language is the URL's. Move to the new one, now that it's saved.
      if (changes.locale && changes.locale !== locale) switchLanguage(changes.locale);
    } catch (error) {
      if (handleAuthError(error)) return;
      if (error instanceof ApiError && error.kind === "validation") {
        const fieldErrors: FieldErrors = {};
        for (const field of error.fields)
          if (field in draft) fieldErrors[field as keyof Draft] = "invalid";
        setErrors(fieldErrors);
        setStatus({ kind: "failed", reason: "invalid" });
        return;
      }
      const reason =
        error instanceof ApiError && (error.kind === "unavailable" || error.kind === "rate_limited")
          ? error.kind
          : "server";
      setStatus({ kind: "failed", reason });
    }
  };

  const fieldError = (key: keyof Draft) =>
    errors[key] ? (
      <span id={`${id}-${key}-error`} className={s.error}>
        <CircleAlert aria-hidden="true" />
        {t(`errors.${errors[key]}`)}
      </span>
    ) : null;
  const described = (key: keyof Draft, hint?: boolean) =>
    [errors[key] ? `${id}-${key}-error` : null, hint ? `${id}-${key}-hint` : null]
      .filter(Boolean)
      .join(" ") || undefined;

  return (
    <div className={`${s.stack} ${s.narrow}`}>
      <header className={s.pageHead}>
        <h1 className={s.pageTitle}>{t("title")}</h1>
        <p className={s.pageLead}>{t("lead")}</p>
      </header>

      <form
        id="profile"
        className={s.panel}
        onSubmit={submit}
        noValidate
        aria-labelledby={`${id}-profile`}
      >
        <div className={s.panelHead}>
          <h2 id={`${id}-profile`} className={s.panelTitle}>
            {t("profileTitle")}
          </h2>
        </div>
        <div className={`${s.panelBody} ${s.form}`}>
          <div className={s.field}>
            <label className={s.label} htmlFor={`${id}-display_name`}>
              {t("name")}
            </label>
            <input
              id={`${id}-display_name`}
              className={s.input}
              value={draft.display_name}
              onChange={(e) => update("display_name", e.target.value)}
              maxLength={NAME_MAX + 20}
              autoComplete="name"
              dir="auto"
              aria-invalid={errors.display_name ? true : undefined}
              aria-describedby={described("display_name", true)}
            />
            <span id={`${id}-display_name-hint`} className={s.hint}>
              {t("nameHint")}
            </span>
            {fieldError("display_name")}
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor={`${id}-email`}>
              {t("email")}
            </label>
            <input
              id={`${id}-email`}
              className={s.input}
              value={user.email ?? ""}
              readOnly
              dir="ltr"
              aria-describedby={`${id}-email-hint`}
            />
            <span id={`${id}-email-hint`} className={s.hint}>
              {t("emailHint")}
            </span>
          </div>

          <h3 className={s.panelTitle}>{t("preferencesTitle")}</h3>

          <div className={s.fieldRow}>
            <div className={s.field}>
              <label className={s.label} htmlFor={`${id}-locale`}>
                {t("language")}
              </label>
              <Select
                id={`${id}-locale`}
                value={draft.locale}
                onValueChange={(value) => update("locale", value as Locale)}
              >
                {routing.locales.map((code) => (
                  <option key={code} value={code} lang={code}>
                    {localeNames[code]}
                  </option>
                ))}
              </Select>
            </div>

            <div className={s.field}>
              <label className={s.label} htmlFor={`${id}-preferred_currency`}>
                {t("currency")}
              </label>
              <Select
                id={`${id}-preferred_currency`}
                value={draft.preferred_currency}
                onValueChange={(value) => update("preferred_currency", value)}
                aria-invalid={errors.preferred_currency ? true : undefined}
                aria-describedby={described("preferred_currency", true)}
              >
                <option value="">{t("currencyNone")}</option>
                {currencies.map(({ code, name }) => (
                  <option key={code} value={code}>
                    {`${code} · ${name}`}
                  </option>
                ))}
              </Select>
              <span id={`${id}-preferred_currency-hint`} className={s.hint}>
                {t("currencyHint")}
              </span>
              {fieldError("preferred_currency")}
            </div>
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor={`${id}-time_zone`}>
              {t("timeZone")}
            </label>
            <Select
              id={`${id}-time_zone`}
              value={draft.time_zone}
              onValueChange={(value) => update("time_zone", value)}
              aria-invalid={errors.time_zone ? true : undefined}
              aria-describedby={described("time_zone", true)}
              dir="ltr"
            >
              {zones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone.replaceAll("_", " ")}
                </option>
              ))}
            </Select>
            <span id={`${id}-time_zone-hint`} className={s.hint}>
              {t("timeZoneHint")}
            </span>
            {device && device !== draft.time_zone && zones.includes(device) ? (
              <button
                type="button"
                className={s.linkBtn}
                onClick={() => update("time_zone", device)}
              >
                {t.rich("useDevice", { zone: device.replaceAll("_", " "), bdi })}
              </button>
            ) : null}
            {fieldError("time_zone")}
          </div>
        </div>

        <div className={s.panelFoot}>
          <Button type="submit" variant="primary" busy={status.kind === "saving"} disabled={!dirty}>
            {status.kind === "saving" ? t("saving") : t("save")}
          </Button>
          {dirty && status.kind !== "saving" ? (
            <Button variant="ghost" onClick={() => (setDraft(draftOf(user)), setErrors({}))}>
              {t("discard")}
            </Button>
          ) : null}
          <span className={s.status} role="status">
            {status.kind === "saved" ? t("saved") : dirty ? t("unsaved") : ""}
          </span>
        </div>
        {status.kind === "failed" ? (
          <div className={cn(s.panelBody, s.flushTop)}>
            <Notice tone="error">{t(`failed.${status.reason}`)}</Notice>
          </div>
        ) : null}
      </form>

      <section className={s.panel} aria-labelledby={`${id}-categories`}>
        <div className={s.panelHead}>
          <h2 id={`${id}-categories`} className={s.panelTitle}>
            {categories("title")}
          </h2>
        </div>
        <div className={s.panelBody}>
          <p className={cn(s.small, s.muted)}>{categories("settingsLead")}</p>
        </div>
        <div className={s.panelFoot}>
          <Link href="/app/settings/categories" className={buttonClass("secondary")}>
            <Tags aria-hidden="true" />
            {categories("manage")}
            <ChevronRight aria-hidden="true" className={s.menuChevron} />
          </Link>
          <Link href="/app/settings/templates" className={buttonClass("secondary")}>
            <Zap aria-hidden="true" />
            {templates("manageLink")}
            <ChevronRight aria-hidden="true" className={s.menuChevron} />
          </Link>
        </div>
      </section>

      <section className={s.panel} aria-labelledby={`${id}-account`}>
        <div className={s.panelHead}>
          <h2 id={`${id}-account`} className={s.panelTitle}>
            {t("accountTitle")}
          </h2>
        </div>
        <div className={s.panelBody}>
          <dl className={s.rows}>
            <div className={s.row}>
              <span className={s.rowText}>
                <dt>{t("signInMethod")}</dt>
                <dd>{t("google")}</dd>
              </span>
              <ShieldCheck aria-hidden="true" width={20} height={20} />
            </div>
            <div className={s.row}>
              <span className={s.rowText}>
                <dt>{t("memberSince")}</dt>
                <dd>
                  <bdi>{formatDate(user.created_at, locale, user.time_zone)}</bdi>
                </dd>
              </span>
            </div>
          </dl>
          <p className={cn(s.small, s.muted, s.noteTop)}>{t("googleNote")}</p>
        </div>
        <div className={s.panelFoot}>
          <Link href="/app/settings/sessions" className={buttonClass("secondary")}>
            <MonitorSmartphone aria-hidden="true" />
            {t("manageSessions")}
            <ChevronRight aria-hidden="true" className={s.menuChevron} />
          </Link>
          <Link href="/app/settings/privacy" className={buttonClass("secondary")}>
            <ShieldCheck aria-hidden="true" />
            {t("managePrivacy")}
            <ChevronRight aria-hidden="true" className={s.menuChevron} />
          </Link>
        </div>
      </section>
    </div>
  );
}
