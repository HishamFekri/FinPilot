"use client";

import { RefreshCw } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { formatDate, formatDateTime, localeTag } from "@/lib/format";
import { type Equivalent, type Equivalents, fxApi } from "@/lib/fx";
import { editableAmount, formatMoney, type Money, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import { AccountDialog } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { Button, Spinner } from "../ui";
import { useFinanceState } from "./finance-provider";
import s from "./finance.module.css";
import { MoneyInput } from "../money-input";
import { Select } from "../select";

/** Favourites people can pick from. Any active currency also works through the API. */
const CHOICES = ["USD", "EUR", "GBP", "TRY", "SAR", "AED", "CHF", "JPY", "XAU_G", "XAU_OZ", "BTC"];

type Fetched = { for: string; data?: Equivalents; failed?: boolean };

/** Fetches equivalents for an amount; `refresh` asks again (the server caches briefly). */
function useEquivalents(amount: Money | null, targets?: string[]) {
  const { handleAuthError } = useAuth();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Fetched | null>(null);
  const key = amount
    ? [amount.amount, amount.currency, targets?.join(",") ?? "", attempt].join("|")
    : null;
  useEffect(() => {
    if (!key) return;
    // Everything the request needs is in the key, so it's the only dependency.
    const [value = "", code = "", list = ""] = key.split("|");
    let cancelled = false;
    fxApi
      .equivalents({ amount: value, currency: code }, list ? list.split(",") : undefined)
      .then((data) => {
        if (!cancelled) setState({ for: key, data });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setState({ for: key, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [key, handleAuthError]);
  const current = state && state.for === key ? state : null;
  return { current, loading: Boolean(key && !current), refresh: () => setAttempt((n) => n + 1) };
}

function decimals(value: string): number {
  return (value.split(".")[1] ?? "").length;
}

/** "7.585 g of gold", "$996.95", "0.01317 BTC": the API's string, formatted, never recalculated. */
function useEquivalentText() {
  const t = useTranslations("app.equivalent");
  const locale = useLocale() as Locale;
  const number = (value: string) =>
    new Intl.NumberFormat(localeTag(locale), {
      minimumFractionDigits: decimals(value),
      maximumFractionDigits: decimals(value),
    }).format(value as Intl.StringNumericLiteral);
  return {
    value: (item: Equivalent) => {
      if (item.value === null) return "";
      if (item.unit === "currency")
        return formatMoney({ amount: item.value, currency: item.target }, locale);
      return t(`units.${item.unit}`, { value: `\u2066${number(item.value)}\u2069` });
    },
    label: (target: string) =>
      target === "XAU_G" || target === "XAU_OZ" || target === "BTC"
        ? t(`targets.${target}`)
        : target,
    price: (item: Equivalent, currency: string) =>
      item.unit_price === null
        ? ""
        : t(`per.${item.unit}`, {
            target: item.target,
            price: formatMoney({ amount: item.unit_price, currency }, locale),
          }),
  };
}

function AsOf({ item }: { item: Equivalent }) {
  const t = useTranslations("app.equivalent");
  const locale = useLocale() as Locale;
  const user = useUser();
  if (item.kind === "spot" && item.as_of) {
    return <>{t("spot", { time: formatDateTime(item.as_of, locale, user.time_zone) })}</>;
  }
  if (item.rate_date) {
    const date = formatDate(`${item.rate_date}T12:00:00Z`, locale, "UTC");
    return <>{item.is_stale ? t("referenceOld", { date }) : t("reference", { date })}</>;
  }
  return null;
}

/** A compact line under a balance: the first few favourites, and a way to see more. */
export function EquivalentsStrip({ amount, onMore }: { amount: Money; onMore: () => void }) {
  const t = useTranslations("app.equivalent");
  const text = useEquivalentText();
  const { current, loading } = useEquivalents(amount);
  const items = (current?.data?.items ?? []).filter((i) => i.status === "ok").slice(0, 3);
  return (
    <div className={s.eqStrip}>
      <span className={s.eqStripLabel}>{t("approx")}</span>
      {loading ? (
        <Spinner />
      ) : items.length === 0 ? (
        <span className={appStyles.muted}>{t("noneNow")}</span>
      ) : (
        items.map((item, i) => (
          <span key={item.target}>
            {i > 0 ? <span aria-hidden="true">{" · "}</span> : null}
            <bdi dir={item.unit === "currency" ? "ltr" : undefined} className={appStyles.num}>
              {text.value(item)}
            </bdi>
          </span>
        ))
      )}
      <button type="button" className={appStyles.linkBtn} onClick={onMore}>
        {t("more")}
      </button>
    </div>
  );
}

/**
 * Amount Equivalent: what an amount is roughly worth in other currencies, gold, or Bitcoin.
 * Display only. Nothing is recorded, no balance changes, and no holding is implied.
 */
export function EquivalentsDialog({
  open,
  onOpenChange,
  initial,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: Money;
}) {
  const t = useTranslations("app.equivalent");
  return (
    <AccountDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("title")}
      lead={t("lead")}
      footer={
        <Button variant="primary" onClick={() => onOpenChange(false)}>
          {t("close")}
        </Button>
      }
    >
      {open ? <Converter key={`${initial.amount}|${initial.currency}`} initial={initial} /> : null}
    </AccountDialog>
  );
}

function Converter({ initial }: { initial: Money }) {
  const t = useTranslations("app.equivalent");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const text = useEquivalentText();
  const id = useId();
  const finance = useFinanceState();
  const known = finance?.load.kind === "ready" ? finance.load.currencies : [];
  const [amountText, setAmountText] = useState(() => editableAmount(initial.amount, locale));
  const [currency, setCurrency] = useState(initial.currency);
  const [editing, setEditing] = useState(false);
  const [favourites, setFavourites] = useState<string[] | null>(null);
  const exponent = known.find((c) => c.code === currency)?.exponent ?? 2;
  const parsed = amountText.trim()
    ? parseAmount(amountText, locale, exponent, { allowNegative: true })
    : null;
  const amount = parsed?.ok ? { amount: parsed.amount, currency } : null;
  const { current, loading, refresh } = useEquivalents(amount, favourites ?? undefined);
  const items = current?.data?.items ?? [];
  const chosen = favourites ?? current?.data?.targets ?? [];

  const toggle = async (target: string) => {
    const next = chosen.includes(target) ? chosen.filter((x) => x !== target) : [...chosen, target];
    if (next.length === 0 || next.length > 12) return;
    setFavourites(next);
    try {
      await fxApi.saveTargets(next);
    } catch (error) {
      handleAuthError(error);
    }
  };

  return (
    <div className={s.eq}>
      <div className={s.eqInput}>
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-amount`}>
            {t("amount")}
          </label>
          <MoneyInput
            id={`${id}-amount`}
            className={appStyles.input}
            value={amountText}
            onValueChange={setAmountText}
          />
        </div>
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-currency`}>
            {t("currency")}
          </label>
          <Select id={`${id}-currency`} value={currency} onValueChange={setCurrency}>
            {[
              ...new Set([
                initial.currency,
                user.preferred_currency ?? "USD",
                "USD",
                "EUR",
                "GBP",
                "TRY",
              ]),
            ].map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div className={s.eqHead}>
        <h3 className={s.subTitle}>{t("resultsTitle")}</h3>
        <span className={s.eqHeadActions}>
          <button type="button" className={appStyles.linkBtn} onClick={() => setEditing((v) => !v)}>
            {editing ? t("doneEditing") : t("edit")}
          </button>
          <button
            type="button"
            className={s.iconText}
            onClick={refresh}
            disabled={loading}
            aria-label={t("refresh")}
          >
            <RefreshCw aria-hidden="true" className={cn(loading && s.spinning)} />
            <span>{t("refresh")}</span>
          </button>
        </span>
      </div>

      {editing ? (
        <fieldset className={s.eqChoices}>
          <legend className={appStyles.srOnly}>{t("edit")}</legend>
          {CHOICES.filter((c) => c !== currency).map((target) => (
            <label key={target} className={s.eqChoice}>
              <input
                type="checkbox"
                checked={chosen.includes(target)}
                onChange={() => void toggle(target)}
              />
              <span>{text.label(target)}</span>
            </label>
          ))}
        </fieldset>
      ) : null}

      {!amount ? (
        <p className={appStyles.muted}>{t("enterAmount")}</p>
      ) : current?.failed ? (
        <p className={appStyles.muted} role="status">
          {t("failed")}
        </p>
      ) : (
        <ul className={s.eqList} aria-busy={loading || undefined}>
          {items.map((item) => (
            <li key={item.target} className={s.eqItem}>
              <span className={s.eqName}>{text.label(item.target)}</span>
              {item.status === "ok" ? (
                <>
                  {/* A currency amount is one left-to-right run; "4.574 g of gold" is a sentence
                      in the reader's language, with only its number isolated. */}
                  <bdi
                    dir={item.unit === "currency" ? "ltr" : undefined}
                    className={cn(appStyles.num, s.eqValue)}
                  >
                    {`≈ ${text.value(item)}`}
                  </bdi>
                  <span className={s.eqMeta}>
                    <bdi dir="ltr">{text.price(item, currency)}</bdi>
                    {" · "}
                    <AsOf item={item} />
                  </span>
                </>
              ) : (
                <span className={cn(s.eqMeta, s.eqUnavailable)}>{t("unavailable")}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className={s.convertFine}>{t("disclaimer")}</p>
    </div>
  );
}
