"use client";

import { ArrowDownLeft, ArrowUpRight, HandCoins } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { localeTag } from "@/lib/format";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import { type Occurrence, type Rule, recurringApi } from "@/lib/recurring";
import { cn } from "@/lib/utils";
import { AccountDialog, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { ConversionPanel, useConversion } from "../finance/conversion";
import { useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { useAccountName, useFinanceError } from "../finance/parts";
import { Button, Notice, Tag } from "../ui";
import s from "./recurring.module.css";
import { MoneyInput } from "../money-input";
import { Select } from "../select";

/** "25–28 Nov", "5 Dec": a window in the reader's language, from plain dates. */
export function useWindowText() {
  const locale = useLocale() as Locale;
  const format = new Intl.DateTimeFormat(localeTag(locale), {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
  return (start: string, end: string) => {
    const a = new Date(`${start}T12:00:00Z`);
    const b = new Date(`${end}T12:00:00Z`);
    return start === end ? format.format(a) : format.formatRange(a, b);
  };
}

/** Income in, expense out, debt instalment: told apart by icon and colour, never colour alone. */
export function KindIcon({ kind, direction }: { kind: Rule["kind"]; direction?: string | null }) {
  const Icon = kind === "income" ? ArrowDownLeft : kind === "expense" ? ArrowUpRight : HandCoins;
  return (
    <span
      className={s.kindIcon}
      data-kind={kind}
      data-direction={direction ?? undefined}
      aria-hidden="true"
    >
      <Icon />
    </span>
  );
}

/** Received (money in) or Paid (money out): what the confirm button says. */
export function moneyIn(occurrence: Pick<Occurrence, "kind" | "debt">): boolean {
  return (
    occurrence.kind === "income" ||
    (occurrence.kind === "debt_payment" && occurrence.debt?.direction === "owed_to_me")
  );
}

export function StateTag({ state }: { state: Occurrence["state"] }) {
  const t = useTranslations("app.recurring.states");
  return (
    <span className={s.state} data-state={state}>
      {t(state)}
    </span>
  );
}

/** One expected payment, with its answers when it's still open. */
export function OccurrenceRow({
  occurrence: o,
  onConfirm,
  onAnswered,
  compact = false,
}: {
  occurrence: Occurrence;
  onConfirm: (o: Occurrence) => void;
  onAnswered: () => void;
  compact?: boolean;
}) {
  const t = useTranslations("app.recurring");
  const locale = useLocale() as Locale;
  const windowText = useWindowText();
  const accountName = useAccountName();
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [busy, setBusy] = useState<"skip" | "notYet" | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const pending = o.status === "pending";

  const answer = async (action: "skip" | "notYet") => {
    setBusy(action);
    setFailure(null);
    try {
      await (action === "skip" ? recurringApi.skip(o.id) : recurringApi.notYet(o.id));
      onAnswered();
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(null);
    }
  };

  const amount = o.actual ?? o.expected;
  return (
    <li className={cn(s.occurrence, compact && s.occurrenceCompact)} data-state={o.state}>
      <KindIcon kind={o.kind} direction={o.debt?.direction} />
      <span className={s.occText}>
        <span className={s.occName} dir="auto">
          {o.rule_name}
          {o.total ? (
            <span className={s.occSeq}>{t("installment", { n: o.sequence, total: o.total })}</span>
          ) : null}
        </span>
        <span className={s.occMeta}>
          <bdi>{windowText(o.window_start, o.window_end)}</bdi>
          {compact ? null : (
            <>
              {" · "}
              <bdi>{accountName(o.account)}</bdi>
            </>
          )}
        </span>
      </span>
      <span className={s.occAmount}>
        {amount ? (
          <bdi dir="ltr" className={cn(appStyles.num, moneyIn(o) && fs.in)}>
            {formatMoney(amount, locale)}
          </bdi>
        ) : (
          <span className={appStyles.muted}>{t("variable")}</span>
        )}
        {o.actual && o.expected && o.actual.amount !== o.expected.amount ? (
          <span className={s.occExpected}>
            {t("expectedWas", { amount: formatMoney(o.expected, locale) })}
          </span>
        ) : null}
        <StateTag state={o.state} />
      </span>
      {pending ? (
        <span className={s.occActions}>
          <Button size="sm" variant="primary" onClick={() => onConfirm(o)}>
            {moneyIn(o) ? t("received") : t("paid")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            busy={busy === "notYet"}
            onClick={() => void answer("notYet")}
          >
            {t("notYet")}
          </Button>
          {compact ? null : (
            <Button
              size="sm"
              variant="ghost"
              busy={busy === "skip"}
              onClick={() => void answer("skip")}
            >
              {t("skip")}
            </Button>
          )}
        </span>
      ) : null}
      {failure ? (
        <Notice tone="error" className={s.occFailure}>
          {failure}
        </Notice>
      ) : null}
    </li>
  );
}

/**
 * Received / Paid: the person confirms what actually happened. Prefilled from the expectation;
 * the amount, date and account can all differ. Posting happens once, on the server (R2).
 */
export function ConfirmDialog({
  occurrence,
  open,
  onClose,
  onDone,
}: {
  occurrence: Occurrence;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const t = useTranslations("app.recurring.confirm");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, currencies, changed } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const currency = occurrence.expected?.currency ?? occurrence.account.currency;
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const [amount, setAmount] = useState(() =>
    occurrence.expected ? editableAmount(occurrence.expected.amount, locale) : "",
  );
  const [accountId, setAccountId] = useState(occurrence.account.id);
  const [date, setDate] = useState(() => {
    const today = todayIn(user.time_zone);
    return occurrence.window_end < today ? occurrence.window_end : today;
  });
  const [accountAmount, setAccountAmount] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);

  const account = accounts.find((a) => a.id === accountId) ?? accounts[0];
  const parsed = amount.trim() ? parseAmount(amount, locale, exponentOf(currency)) : null;
  const isDebt = occurrence.kind === "debt_payment";
  const cross = Boolean(account && account.currency !== currency);
  const conversion = useConversion({
    active: cross && !isDebt,
    from: currency,
    to: account?.currency ?? currency,
    amount: parsed?.ok ? parsed.amount : null,
    date,
    toExponent: exponentOf(account?.currency ?? currency),
    fromExponent: exponentOf(currency),
  });
  const accountParsed =
    isDebt && cross && accountAmount.trim() && account
      ? parseAmount(accountAmount, locale, exponentOf(account.currency))
      : null;
  const today = todayIn(user.time_zone);
  const incoming = moneyIn(occurrence);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!parsed?.ok || !account || !date || date > today) return;
    if (cross && !isDebt && !conversion.choice) return;
    if (cross && isDebt && !accountParsed?.ok) return;
    setBusy(true);
    try {
      await recurringApi.confirm(
        occurrence.id,
        {
          amount: { amount: parsed.amount, currency },
          account_id: account.id,
          effective_date: date,
          ...(conversion.choice && cross && !isDebt ? { conversion: conversion.choice } : {}),
          ...(accountParsed?.ok ? { account_amount: accountParsed.amount } : {}),
        },
        key,
      );
      await changed();
      onDone();
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  const titleKey = isDebt
    ? incoming
      ? "titleRepaid"
      : "titleInstallment"
    : incoming
      ? "titleReceived"
      : "titlePaid";
  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t(titleKey, { name: occurrence.rule_name })}
      lead={
        occurrence.expected
          ? t("lead", { amount: formatMoney(occurrence.expected, locale) })
          : t("leadVariable")
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {incoming ? t("confirmReceived") : t("confirmPaid")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {incoming ? t("amountReceived") : t("amountPaid")}
          </label>
          <div className={fs.inlineMoney}>
            <bdi dir="ltr" className={fs.amountCurrency}>
              {currency}
            </bdi>
            <MoneyInput
              id={`${id}-amount`}
              className={appStyles.input}
              value={amount}
              onValueChange={setAmount}
              aria-invalid={(parsed && !parsed.ok) || (submitted && !parsed) ? true : undefined}
            />
          </div>
          <span className={parsed && !parsed.ok ? appStyles.error : appStyles.hint}>
            {parsed && !parsed.ok
              ? amountT(`errors.${parsed.error}`, {
                  example: formatMoney({ amount: "1250.5", currency: currency }, locale, {
                    display: "none",
                  }),
                  currency,
                  count: exponentOf(currency),
                })
              : submitted && !parsed
                ? amountT("errors.empty")
                : t("amountHint")}
          </span>
        </div>
        <div className={fs.fieldsRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-account`}>
              {incoming ? t("into") : t("from")}
            </label>
            <Select id={`${id}-account`} value={account?.id ?? ""} onValueChange={setAccountId}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {`${accountName(a)} · ${a.currency}`}
                </option>
              ))}
            </Select>
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-date`}>
              {incoming ? t("dateReceived") : t("datePaid")}
            </label>
            <input
              id={`${id}-date`}
              type="date"
              className={appStyles.input}
              value={date}
              max={today}
              onChange={(event) => setDate(event.target.value)}
            />
          </div>
        </div>
        {cross && !isDebt && account ? (
          <ConversionPanel
            conversion={conversion}
            accountLabel={accountName(account)}
            kind={incoming ? "income" : "expense"}
            showErrors={submitted}
          />
        ) : null}
        {cross && isDebt && account ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-acct-amount`}>
              {t("accountAmount", { account: accountName(account), currency: account.currency })}
            </label>
            <MoneyInput
              id={`${id}-acct-amount`}
              className={appStyles.input}
              value={accountAmount}
              onValueChange={setAccountAmount}
              aria-invalid={submitted && !accountParsed?.ok ? true : undefined}
            />
          </div>
        ) : null}
        <p className={fs.convertFine}>{t("onlyNow")}</p>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        {occurrence.state === "overdue" ? <Tag>{t("overdueNote")}</Tag> : null}
      </form>
    </AccountDialog>
  );
}
