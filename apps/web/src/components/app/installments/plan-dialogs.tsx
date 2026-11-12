"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import { type InstallmentPlan, installmentsApi } from "@/lib/installments";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import { AccountDialog, AmountField, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import { useAccountName } from "../finance/parts";
import { Select } from "../select";
import { Button, Notice } from "../ui";
import { useInstallmentError } from "./errors";
import s from "./installments.module.css";

function minus(a: string, b: string): string {
  // Display defaults only: the API checks every amount again.
  const [ai = "0", af = ""] = a.split(".");
  const [bi = "0", bf = ""] = b.split(".");
  const places = Math.max(af.length, bf.length);
  const big = (i: string, f: string) => BigInt(i + f.padEnd(places, "0"));
  const diff = big(ai, af) - big(bi, bf);
  const zero = BigInt(0);
  const text = (diff < zero ? zero : diff).toString().padStart(places + 1, "0");
  return places ? `${text.slice(0, -places)}.${text.slice(-places)}` : text;
}

/**
 * Records a real payment. Principal is a repayment (never spending); interest and fees, if the
 * agreement has them, are separate expenses in "Interest & fees". The schedule never pays itself.
 */
export function PayDialog({
  plan,
  open,
  onOpenChange,
  onPaid,
}: {
  plan: InstallmentPlan;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPaid: (plan: InstallmentPlan) => void;
}) {
  const t = useTranslations("app.installments.pay");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const message = useInstallmentError();
  const { accounts, currencies, defaultAccountId, changed } = useFinance();
  const accountName = useAccountName();
  const id = useId();
  const next = plan.next_due;
  const exponent = currencies.find((c) => c.code === plan.currency)?.exponent ?? 2;
  const active = accounts.filter((a) => a.status === "active");
  // Prefer an account in the plan's currency: the default one, else the first.
  const sameCurrency = active.filter((a) => a.currency === plan.currency);
  const preferred =
    sameCurrency.find((a) => a.id === defaultAccountId) ?? sameCurrency[0] ?? active[0];

  const [accountId, setAccountId] = useState(preferred?.id ?? "");
  const [principal, setPrincipal] = useState(
    next ? editableAmount(minus(next.principal.amount, next.covered.amount), locale) : "",
  );
  const [accountAmount, setAccountAmount] = useState("");
  const [interest, setInterest] = useState(
    next && Number(next.interest.amount) > 0 ? editableAmount(next.interest.amount, locale) : "",
  );
  const [fee, setFee] = useState(
    next && Number(next.fee.amount) > 0 ? editableAmount(next.fee.amount, locale) : "",
  );
  const [paidOn, setPaidOn] = useState(todayIn(user.time_zone));
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [key, setKey] = useState(() => crypto.randomUUID());
  const account = active.find((a) => a.id === accountId);
  const crossCurrency = account !== undefined && account.currency !== plan.currency;
  const accountExponent = currencies.find((c) => c.code === account?.currency)?.exponent ?? 2;
  const value = (text: string, exp = exponent) => {
    const parsed = text.trim() ? parseAmount(text, locale, exp) : null;
    return parsed?.ok ? parsed.amount : null;
  };

  const submit = async () => {
    setSubmitted(true);
    setFailure(null);
    if (!accountId) return;
    const body = {
      account_id: accountId,
      principal: value(principal),
      principal_account_amount: crossCurrency ? value(accountAmount, accountExponent) : null,
      interest: value(interest),
      fee: value(fee),
      paid_on: paidOn,
    };
    if (!body.principal && !body.interest && !body.fee) return;
    if (crossCurrency && body.principal && !body.principal_account_amount) return;
    setBusy(true);
    try {
      const updated = await installmentsApi.pay(plan.id, body, key);
      await changed();
      onPaid(updated);
      onOpenChange(false);
      setKey(crypto.randomUUID());
    } catch (error) {
      if (!handleAuthError(error)) {
        setFailure(message(error));
        // A refused payment can be corrected and sent again under a new key.
        if (error instanceof ApiError && error.kind !== "unavailable") setKey(crypto.randomUUID());
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("title", { name: plan.name })}
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button variant="primary" busy={busy} onClick={() => void submit()}>
            {t("save")}
          </Button>
        </>
      }
    >
      <div className={appStyles.form}>
        {next ? (
          <p className={s.note}>
            {t("next", {
              sequence: next.sequence,
              amount: formatMoney(next.total, locale),
            })}
          </p>
        ) : null}
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-account`}>
            {t("account")}
          </label>
          <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
            {active.map((a) => (
              <option key={a.id} value={a.id}>
                {`${accountName(a)} · ${a.currency}`}
              </option>
            ))}
          </Select>
        </div>
        <AmountField
          label={t("principal")}
          hint={t("principalHint")}
          currency={plan.currency}
          exponent={exponent}
          value={principal}
          onChange={setPrincipal}
          showErrors={submitted}
          required={false}
        />
        {crossCurrency && account ? (
          <AmountField
            label={t("accountAmount", { currency: account.currency })}
            hint={t("accountAmountHint")}
            currency={account.currency}
            exponent={accountExponent}
            value={accountAmount}
            onChange={setAccountAmount}
            showErrors={submitted}
          />
        ) : null}
        <div className={appStyles.fieldRow}>
          <AmountField
            label={t("interest")}
            currency={plan.currency}
            exponent={exponent}
            value={interest}
            onChange={setInterest}
            showErrors={submitted}
            required={false}
          />
          <AmountField
            label={t("fee")}
            currency={plan.currency}
            exponent={exponent}
            value={fee}
            onChange={setFee}
            showErrors={submitted}
            required={false}
          />
        </div>
        <p className={appStyles.hint}>{t("interestHint")}</p>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-date`}>
            {t("date")}
          </label>
          <input
            id={`${id}-date`}
            type="date"
            className={appStyles.input}
            value={paidOn}
            max={todayIn(user.time_zone)}
            onChange={(e) => setPaidOn(e.target.value)}
          />
        </div>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </div>
    </AccountDialog>
  );
}

/** New dates or amounts for what isn't paid yet. The old schedule is kept, marked replaced. */
export function RescheduleDialog({
  plan,
  open,
  onOpenChange,
  onDone,
}: {
  plan: InstallmentPlan;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: (plan: InstallmentPlan) => void;
}) {
  const t = useTranslations("app.installments.reschedule");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useInstallmentError();
  const { currencies, changed } = useFinance();
  const id = useId();
  const exponent = currencies.find((c) => c.code === plan.currency)?.exponent ?? 2;
  const [count, setCount] = useState(String(plan.remaining_count || 1));
  const [first, setFirst] = useState(plan.next_due?.due_date ?? "");
  const [interest, setInterest] = useState(
    plan.next_due && Number(plan.next_due.interest.amount) > 0
      ? editableAmount(plan.next_due.interest.amount, locale)
      : "",
  );
  const [fee, setFee] = useState(
    plan.next_due && Number(plan.next_due.fee.amount) > 0
      ? editableAmount(plan.next_due.fee.amount, locale)
      : "",
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const value = (text: string) => {
    const parsed = text.trim() ? parseAmount(text, locale, exponent) : null;
    return parsed?.ok ? parsed.amount : null;
  };

  const submit = async () => {
    setFailure(null);
    if (!first || !(Number(count) >= 1)) return;
    setBusy(true);
    try {
      const updated = await installmentsApi.reschedule(plan.id, plan.version, {
        remaining_count: Number(count),
        first_due_date: first,
        interest_per_installment: value(interest),
        fee_per_installment: value(fee),
      });
      await changed();
      onDone(updated);
      onOpenChange(false);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("title")}
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button variant="primary" busy={busy} onClick={() => void submit()}>
            {t("save")}
          </Button>
        </>
      }
    >
      <div className={appStyles.form}>
        <div className={appStyles.fieldRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-count`}>
              {t("count")}
            </label>
            <input
              id={`${id}-count`}
              className={appStyles.input}
              inputMode="numeric"
              value={count}
              onChange={(e) => setCount(e.target.value.replace(/\D/g, "").slice(0, 3))}
            />
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-first`}>
              {t("first")}
            </label>
            <input
              id={`${id}-first`}
              type="date"
              className={appStyles.input}
              value={first}
              onChange={(e) => setFirst(e.target.value)}
            />
          </div>
        </div>
        <div className={appStyles.fieldRow}>
          <AmountField
            label={t("interest")}
            currency={plan.currency}
            exponent={exponent}
            value={interest}
            onChange={setInterest}
            showErrors={false}
            required={false}
          />
          <AmountField
            label={t("fee")}
            currency={plan.currency}
            exponent={exponent}
            value={fee}
            onChange={setFee}
            showErrors={false}
            required={false}
          />
        </div>
        <p className={appStyles.hint}>{t("hint")}</p>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </div>
    </AccountDialog>
  );
}
