"use client";

import { ArrowDown } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import type { ConversionChoice } from "@/lib/finance";
import { formatDate, formatDateTime } from "@/lib/format";
import { type ConvertResult, fxApi } from "@/lib/fx";
import { formatMoney, padAmount, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import appStyles from "../app.module.css";
import { useUser } from "../auth-provider";
import s from "./finance.module.css";
import { MoneyInput } from "../money-input";

type Method = ConversionChoice["method"];
type Fetched = {
  for: string;
  result?: ConvertResult;
  error?: "unavailable" | "unsupported" | "invalid";
};

function classify(error: unknown): NonNullable<Fetched["error"]> {
  if (error instanceof ApiError) {
    if (error.code === "fx_pair_unsupported") return "unsupported";
    if (error.code === "invalid_rate" || error.kind === "validation") return "invalid";
  }
  return "unavailable";
}

/** A typed rate in any of the three languages' habits: "0,9", "0.9", "٠٫٩". Never a guess. */
export function normalizeRate(text: string): string | null {
  const latin = text
    .trim()
    .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
    .replace(/[٫,]/g, ".");
  return /^(0|[1-9]\d{0,11})(\.\d{1,12})?$/.test(latin) && Number(latin) > 0 ? latin : null;
}

/**
 * Everything the conversion panel needs, owned by the form that saves. The backend does every
 * calculation (M5): a reference preview, a typed-rate preview, or nothing at all when the person
 * types the amount actually charged, which is recorded exactly.
 */
export function useConversion({
  active,
  from,
  to,
  amount,
  date,
  toExponent,
  fromExponent = 2,
}: {
  active: boolean;
  from: string;
  to: string;
  amount: string | null;
  date: string;
  toExponent: number;
  fromExponent?: number;
}) {
  const locale = useLocale() as Locale;
  const [method, setMethod] = useState<Method>("reference");
  const [charged, setCharged] = useState("");
  const [rateText, setRateText] = useState("");
  const [acceptedRateId, setAcceptedRateId] = useState<string | null>(null);
  const [reference, setReference] = useState<Fetched | null>(null);
  const [typed, setTyped] = useState<Fetched | null>(null);

  const referenceKey = active && amount ? `${from}|${to}|${amount}|${date}` : null;
  useEffect(() => {
    if (!referenceKey || !amount) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fxApi
        .convert({ from, to, amount, date })
        .then((result) => {
          if (!cancelled) setReference({ for: referenceKey, result });
        })
        .catch((error: unknown) => {
          if (!cancelled) setReference({ for: referenceKey, error: classify(error) });
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [referenceKey, from, to, amount, date]);

  const rate = normalizeRate(rateText);
  const typedKey =
    active && amount && rate && method === "manual_rate" ? `${referenceKey}|${rate}` : null;
  useEffect(() => {
    if (!typedKey || !amount || !rate) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fxApi
        .convert({ from, to, amount, rate })
        .then((result) => {
          if (!cancelled) setTyped({ for: typedKey, result });
        })
        .catch((error: unknown) => {
          if (!cancelled) setTyped({ for: typedKey, error: classify(error) });
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [typedKey, from, to, amount, rate]);

  const ref = reference && reference.for === referenceKey ? reference : null;
  const typedNow = typed && typed.for === typedKey ? typed : null;
  const view = ref?.result?.rate ?? null;
  // Without a usable reference, the reference option isn't offered.
  const effective: Method = method === "reference" && ref?.error ? "actual_amount" : method;
  const chargedParsed = charged.trim() ? parseAmount(charged, locale, toExponent) : null;
  const stale = Boolean(view && (view.is_stale || view.fallback));
  const staleAccepted = Boolean(view && acceptedRateId === view.id);

  let choice: ConversionChoice | null = null;
  if (effective === "reference" && view && (!stale || staleAccepted)) {
    choice = { method: "reference", rate_id: view.id, ...(stale ? { accept_stale: true } : {}) };
  } else if (effective === "actual_amount" && chargedParsed?.ok) {
    choice = {
      method: "actual_amount",
      account_amount: chargedParsed.amount,
      ...(view ? { rate_id: view.id } : {}),
    };
  } else if (effective === "manual_rate" && rate && typedNow?.result) {
    choice = { method: "manual_rate", rate };
  }

  return {
    active,
    from,
    to,
    amount,
    toExponent,
    fromExponent,
    method: effective,
    setMethod,
    charged,
    setCharged,
    chargedParsed,
    rateText,
    setRateText,
    rateValid: rate !== null,
    reference: ref,
    loading: Boolean(referenceKey && !ref),
    typed: typedNow,
    stale,
    staleAccepted,
    acceptStale: (yes: boolean) => setAcceptedRateId(yes && view ? view.id : null),
    choice,
    reset: () => {
      setCharged("");
      setRateText("");
      setAcceptedRateId(null);
      setMethod("reference");
    },
  };
}

export type Conversion = ReturnType<typeof useConversion>;

function sourceLabel(
  t: ReturnType<typeof useTranslations<"app.fx">>,
  upstream: string | null,
  date: string,
): string {
  return upstream === "ECB" ? t("sourceEcb", { date }) : t("sourceBlended", { date });
}

/**
 * The conversion step, shown only when the amount's currency isn't the account's. It always says
 * what was entered, in which currency, which account changes, and whether the result is an
 * estimate (a reference rate) or exact (the amount charged).
 */
export function ConversionPanel({
  conversion: c,
  accountLabel,
  kind,
  showErrors,
}: {
  conversion: Conversion;
  accountLabel: string;
  kind: "expense" | "income";
  showErrors: boolean;
}) {
  const t = useTranslations("app.fx");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const id = useId();
  if (!c.active) return null;

  const view = c.reference?.result?.rate ?? null;
  const estimate = c.reference?.result?.converted ?? null;
  const original = c.amount
    ? { amount: padAmount(c.amount, c.fromExponent), currency: c.from }
    : null;
  const date = (iso: string) => formatDate(`${iso}T12:00:00Z`, locale, "UTC");
  const money = (value: { amount: string; currency: string }) => (
    <bdi dir="ltr" className={appStyles.num}>
      {formatMoney(value, locale)}
    </bdi>
  );

  const methods: Conversion["method"][] = c.reference?.error
    ? ["actual_amount", "manual_rate"]
    : ["reference", "actual_amount", "manual_rate"];

  return (
    <section className={s.convert} aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`} className={s.convertTitle}>
        {t("title")}
      </h3>

      <dl className={s.convertFlow}>
        <div>
          <dt>{t("entered")}</dt>
          <dd>
            {original ? money(original) : <span className={appStyles.muted}>{t("noAmount")}</span>}
          </dd>
        </div>
        <ArrowDown aria-hidden="true" className={s.convertArrow} />
        <div>
          <dt>
            {kind === "expense"
              ? t("takenFrom", { account: accountLabel })
              : t("addedTo", { account: accountLabel })}
          </dt>
          <dd aria-live="polite">
            {c.method === "actual_amount" && c.chargedParsed?.ok ? (
              <>
                {money({ amount: c.chargedParsed.amount, currency: c.to })}{" "}
                <span className={s.exact}>{t("exact")}</span>
              </>
            ) : c.method === "manual_rate" && c.typed?.result?.converted ? (
              <>
                {money(c.typed.result.converted)}{" "}
                <span className={s.estimate}>{t("yourRate")}</span>
              </>
            ) : c.method === "reference" && estimate ? (
              <>
                {"≈ "}
                {money(estimate)} <span className={s.estimate}>{t("estimate")}</span>
              </>
            ) : (
              <bdi dir="ltr" className={appStyles.muted}>
                {c.to}
              </bdi>
            )}
          </dd>
        </div>
      </dl>

      {c.loading ? (
        <p className={s.convertNote} role="status">
          {t("loading")}
        </p>
      ) : c.reference?.error ? (
        <p className={cn(s.convertNote, s.convertWarn)} role="status">
          {c.reference.error === "unsupported"
            ? t("unsupported", { from: c.from, to: c.to })
            : t("unavailable")}
        </p>
      ) : view ? (
        <p className={s.convertNote}>
          <bdi dir="ltr" className={appStyles.num}>
            {t("rateLine", { from: c.from, rate: view.rate, to: c.to })}
          </bdi>
          {" · "}
          {sourceLabel(t, view.upstream, date(view.rate_date))}
          {" · "}
          {t("checked", { time: formatDateTime(view.retrieved_at, locale, user.time_zone) })}
        </p>
      ) : null}

      {view && c.stale && c.method === "reference" ? (
        <div className={s.staleBox}>
          <p>
            {view.fallback ? t("fallback") : null}{" "}
            {t("stale", { date: date(view.rate_date), days: view.age_days })}
          </p>
          <label className={s.checkRow}>
            <input
              type="checkbox"
              checked={c.staleAccepted}
              onChange={(event) => c.acceptStale(event.target.checked)}
            />
            <span>{t("staleAccept")}</span>
          </label>
        </div>
      ) : null}

      <fieldset className={s.methods}>
        <legend className={appStyles.srOnly}>{t("how")}</legend>
        {methods.map((m) => (
          <label key={m} className={s.methodOption}>
            <input
              type="radio"
              name={`${id}-method`}
              checked={c.method === m}
              onChange={() => c.setMethod(m)}
            />
            <span>{t(`methods.${m}`)}</span>
          </label>
        ))}
      </fieldset>

      {c.method === "actual_amount" ? (
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-charged`}>
            {t("chargedLabel", { account: accountLabel, currency: c.to })}
          </label>
          <MoneyInput
            id={`${id}-charged`}
            className={appStyles.input}
            value={c.charged}
            onValueChange={c.setCharged}
            aria-invalid={c.chargedParsed && !c.chargedParsed.ok ? true : undefined}
          />
          <span
            className={c.chargedParsed && !c.chargedParsed.ok ? appStyles.error : appStyles.hint}
          >
            {c.chargedParsed && !c.chargedParsed.ok
              ? amountT(`errors.${c.chargedParsed.error}`, {
                  example: formatMoney(
                    { amount: c.toExponent ? "1250.5" : "1250", currency: c.to },
                    locale,
                    { display: "none" },
                  ),
                  currency: c.to,
                  count: c.toExponent,
                })
              : showErrors && !c.chargedParsed
                ? t("chargedNeeded")
                : t("chargedHint")}
          </span>
        </div>
      ) : null}

      {c.method === "manual_rate" ? (
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-rate`}>
            <bdi dir="ltr">{t("manualLabel", { from: c.from, to: c.to })}</bdi>
          </label>
          <input
            id={`${id}-rate`}
            className={appStyles.input}
            inputMode="decimal"
            autoComplete="off"
            dir="ltr"
            value={c.rateText}
            onChange={(event) => c.setRateText(event.target.value)}
            aria-invalid={c.rateText && !c.rateValid ? true : undefined}
          />
          <span className={c.rateText && !c.rateValid ? appStyles.error : appStyles.hint}>
            {c.rateText && !c.rateValid ? t("rateInvalid") : t("manualHint")}
          </span>
        </div>
      ) : null}

      {showErrors && !c.choice ? (
        <p className={cn(s.echo, s.echoError)} role="alert">
          {t("needsChoice")}
        </p>
      ) : (
        <p className={s.convertFine}>{t("referenceNote")}</p>
      )}
    </section>
  );
}
