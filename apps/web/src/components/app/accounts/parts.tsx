"use client";

import { Dialog } from "@base-ui/react/dialog";
import { Banknote, Landmark, PiggyBank, Smartphone, Wallet } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useId } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import type { AccountType } from "@/lib/accounts";
import { formatMoney, isNegative, type Money as MoneyValue, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import appStyles from "../app.module.css";
import { usePortalRoot } from "../app-shell";
import s from "./accounts.module.css";
import { MoneyInput, useMoneyHint } from "../money-input";

export const TYPE_ICONS: Record<AccountType, typeof Wallet> = {
  cash: Banknote,
  bank: Landmark,
  wallet: Smartphone,
  savings: PiggyBank,
  other: Wallet,
};

/**
 * The one way an amount appears (ARCHITECTURE.md §14.5): formatted by the reader's language, in
 * its own left-to-right run, so Arabic text around it can never reorder its digits or sign.
 */
export function Money({
  value,
  display = "symbol",
  signed = false,
  className,
}: {
  value: MoneyValue;
  display?: "symbol" | "code";
  signed?: boolean;
  className?: string;
}) {
  const locale = useLocale() as Locale;
  return (
    <bdi dir="ltr" className={cn(appStyles.num, isNegative(value) && s.negative, className)}>
      {formatMoney(value, locale, { display, signed })}
    </bdi>
  );
}

export type AmountState = { text: string; parsed: ReturnType<typeof parseAmount> | null };

/**
 * An amount input in the reader's language. It accepts their separators (and Arabic-Indic
 * digits), refuses anything ambiguous, and echoes back how it read the number before saving (M7).
 */
export function AmountField({
  label,
  hint,
  currency,
  exponent,
  value,
  onChange,
  allowNegative = false,
  showErrors,
  required = true,
}: {
  label: string;
  hint?: string;
  currency: string;
  exponent: number;
  value: string;
  onChange: (text: string) => void;
  allowNegative?: boolean;
  showErrors: boolean;
  required?: boolean;
}) {
  const t = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const id = useId();
  const money = useMoneyHint();
  const parsed = value.trim() ? parseAmount(value, locale, exponent, { allowNegative }) : null;
  const error =
    parsed && !parsed.ok
      ? parsed.error
      : !parsed && required && showErrors
        ? ("empty" as const)
        : null;
  const example = formatMoney({ amount: exponent ? "1250.5" : "1250", currency }, locale, {
    display: "none",
  });

  return (
    <div className={appStyles.field}>
      <label className={appStyles.label} htmlFor={id}>
        {label}
      </label>
      <div className={s.amountWrap}>
        <span className={s.amountCode} aria-hidden="true">
          {currency}
        </span>
        <MoneyInput
          id={id}
          className={cn(appStyles.input, s.amountInput)}
          value={value}
          onValueChange={onChange}
          allowNegative={allowNegative}
          onRejected={money.onRejected}
          aria-invalid={error && (showErrors || parsed) ? true : undefined}
          aria-describedby={`${id}-hint ${id}-echo`}
        />
      </div>
      {hint ? (
        <span id={`${id}-hint`} className={appStyles.hint}>
          {hint}
        </span>
      ) : null}
      <span id={`${id}-echo`} className={error ? appStyles.error : s.echo} aria-live="polite">
        {money.hint
          ? money.hint
          : error
            ? t(`errors.${error}`, { example, currency, count: exponent })
            : parsed?.ok
              ? t.rich("reads", {
                  amount: formatMoney({ amount: parsed.amount, currency }, locale),
                  bdi: (chunks) => <bdi dir="ltr">{chunks}</bdi>,
                })
              : ""}
      </span>
    </div>
  );
}

/** A centred dialog in the shell's theme: title, a scrolling body, and a footer of actions. */
export function AccountDialog({
  open,
  onOpenChange,
  title,
  lead,
  children,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  lead?: ReactNode;
  children: ReactNode;
  footer: ReactNode;
}) {
  const root = usePortalRoot();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal container={root}>
        <Dialog.Backdrop className={appStyles.dialogBackdrop} />
        <Dialog.Popup className={s.dialog}>
          <div className={s.dialogHead}>
            <Dialog.Title className={s.dialogTitle}>{title}</Dialog.Title>
            {lead ? <Dialog.Description className={s.dialogLead}>{lead}</Dialog.Description> : null}
          </div>
          <div className={s.dialogBody}>{children}</div>
          <div className={s.dialogFoot}>{footer}</div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const KNOWN_CODES = new Set([
  "account_name_taken",
  "balance_changed",
  "account_archived",
  "same_account",
  "received_amount_required",
  "amounts_differ",
  "amount_not_positive",
  "invalid_amount",
  "unsupported_currency",
  "currency_mismatch",
  "date_in_future",
  "version_conflict",
  "balance_not_zero",
  "no_change",
  "not_found",
]);

/** A translated message for any failure. The API's own text is never shown (API_SPEC.md §1.8). */
export function useErrorMessage() {
  const t = useTranslations("app.accounts.errors");
  return (error: unknown): string => {
    if (error instanceof ApiError) {
      if (error.kind === "unavailable") return t("unavailable");
      if (error.kind === "rate_limited") return t("rate_limited");
      if (error.code && KNOWN_CODES.has(error.code)) return t(error.code as "not_found");
    }
    return t("generic");
  };
}

/** Today's date in the user's time zone, as YYYY-MM-DD, for date inputs. */
export function todayIn(timeZone: string): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date());
  } catch {
    return new Intl.DateTimeFormat("en-CA").format(new Date());
  }
}
