"use client";

import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import {
  type Account,
  accountsApi,
  type CurrencyInfo,
  newIdempotencyKey,
  type TransferPreview,
} from "@/lib/accounts";
import { formatMoney, parseAmount } from "@/lib/money";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { bdi, Button, Notice } from "../ui";
import { useAccountName } from "../finance/parts";
import s from "./accounts.module.css";
import { AccountDialog, AmountField, todayIn, useErrorMessage } from "./parts";
import { Select } from "../select";

/**
 * Moves recorded money between two of the person's accounts. Not income, not spending.
 *
 * Across currencies, both real amounts are entered: what left, and what arrived. The backend
 * reports the implied rate (it's never computed here, M5), and the person confirms it matches what
 * they actually got before anything is recorded. FinPilot never supplies a rate of its own.
 */
export function TransferDialog({
  accounts,
  initialFromId,
  currencies,
  open,
  onOpenChange,
  onTransferred,
}: {
  accounts: Account[];
  initialFromId?: string;
  currencies: CurrencyInfo[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTransferred: (changed: Account[]) => void;
}) {
  const t = useTranslations("app.accounts.transfer");
  const common = useTranslations("app.accounts.form");
  const accountName = useAccountName();
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const message = useErrorMessage();
  const id = useId();

  const firstFrom = initialFromId ?? accounts[0]?.id ?? "";
  const firstTo = (id: string) => accounts.find((a) => a.id !== id)?.id ?? "";
  const [fromId, setFromId] = useState(firstFrom);
  const [toId, setToId] = useState(firstTo(firstFrom));
  const [amount, setAmount] = useState("");
  const [received, setReceived] = useState("");
  const [date, setDate] = useState(() => todayIn(user.time_zone));
  const [note, setNote] = useState("");
  const [rateConfirmed, setRateConfirmed] = useState(false);
  // The preview, tagged with the inputs it was asked for: a stale one is never shown.
  const [preview, setPreview] = useState<{ for: string; result: TransferPreview } | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);

  const source = accounts.find((a) => a.id === fromId);
  const destination = accounts.find((a) => a.id === toId);
  const exponentOf = (code?: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const crossCurrency = Boolean(source && destination && source.currency !== destination.currency);
  const sent = source ? parseAmount(amount, locale, exponentOf(source.currency)) : null;
  const got =
    destination && crossCurrency
      ? parseAmount(received, locale, exponentOf(destination.currency))
      : null;
  const today = todayIn(user.time_zone);

  // Across currencies, ask the backend for the implied rate once both amounts read correctly.
  const sentAmount = sent?.ok ? sent.amount : null;
  const gotAmount = got?.ok ? got.amount : null;
  const previewFor = [source?.id, destination?.id, sentAmount, gotAmount].join("|");
  useEffect(() => {
    if (!crossCurrency || !source || !destination || !sentAmount || !gotAmount) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      accountsApi
        .previewTransfer(
          source.id,
          destination.id,
          { amount: sentAmount, currency: source.currency },
          { amount: gotAmount, currency: destination.currency },
        )
        .then((result) => {
          if (!cancelled) setPreview({ for: previewFor, result });
        })
        .catch(() => {
          if (!cancelled) setPreview(null);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [crossCurrency, source, destination, sentAmount, gotAmount, previewFor]);

  const shownPreview =
    crossCurrency && sentAmount && gotAmount && preview?.for === previewFor ? preview.result : null;

  const reset = () => {
    setFromId(firstFrom);
    setToId(firstTo(firstFrom));
    setAmount("");
    setReceived("");
    setDate(todayIn(user.time_zone));
    setNote("");
    setRateConfirmed(false);
    setPreview(null);
    setSubmitted(false);
    setFailure(null);
    setKey(newIdempotencyKey());
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!source || !destination || !sent?.ok) return;
    if (crossCurrency && (!got?.ok || !rateConfirmed || !shownPreview)) return;
    setBusy(true);
    try {
      const result = await accountsApi.transfer(
        {
          from_account_id: source.id,
          to_account_id: destination.id,
          amount: { amount: sent.amount, currency: source.currency },
          ...(crossCurrency && got?.ok
            ? { received_amount: { amount: got.amount, currency: destination.currency } }
            : {}),
          effective_date: date,
          description: note.trim() || null,
        },
        key,
      );
      onTransferred([result.from_account, result.to_account]);
      onOpenChange(false);
      reset();
    } catch (error) {
      if (handleAuthError(error)) return;
      setFailure(message(error));
      if (error instanceof ApiError && error.kind !== "unavailable") setKey(newIdempotencyKey());
    } finally {
      setBusy(false);
    }
  };

  const option = (a: Account) =>
    `${accountName(a)} · ${formatMoney(a.balance, locale, { display: "code" })}`;

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (next) reset();
        onOpenChange(next);
      }}
      title={t("title")}
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {common("cancel")}
          </Button>
          <Button
            type="submit"
            form={`${id}-form`}
            variant="primary"
            busy={busy}
            disabled={accounts.length < 2}
          >
            {busy ? t("submitting") : t("submit")}
          </Button>
        </>
      }
    >
      {accounts.length < 2 ? (
        <Notice tone="info">{t("needTwo")}</Notice>
      ) : (
        <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
          <div className={appStyles.fieldRow}>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-from`}>
                {t("from")}
              </label>
              <Select
                id={`${id}-from`}
                value={fromId}
                onValueChange={(value) => {
                  const next = value;
                  setFromId(next);
                  if (next === toId) setToId(firstTo(next));
                  setRateConfirmed(false);
                }}
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {option(a)}
                  </option>
                ))}
              </Select>
            </div>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-to`}>
                {t("to")}
              </label>
              <Select
                id={`${id}-to`}
                value={toId}
                onValueChange={(value) => {
                  setToId(value);
                  setRateConfirmed(false);
                }}
              >
                {accounts
                  .filter((a) => a.id !== fromId)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {option(a)}
                    </option>
                  ))}
              </Select>
            </div>
          </div>

          {source ? (
            <AmountField
              label={crossCurrency ? t("sent") : t("amount")}
              currency={source.currency}
              exponent={exponentOf(source.currency)}
              value={amount}
              onChange={(text) => {
                setAmount(text);
                setRateConfirmed(false);
              }}
              showErrors={submitted}
            />
          ) : null}

          {crossCurrency && destination ? (
            <>
              <AmountField
                label={t("received")}
                hint={t("receivedHint", { currency: destination.currency })}
                currency={destination.currency}
                exponent={exponentOf(destination.currency)}
                value={received}
                onChange={(text) => {
                  setReceived(text);
                  setRateConfirmed(false);
                }}
                showErrors={submitted}
              />
              <div className={s.review} aria-live="polite">
                {shownPreview?.implied_rate ? (
                  <p>
                    {t.rich("rate", {
                      from: source?.currency ?? "",
                      to: destination.currency,
                      rate: shownPreview.implied_rate,
                      bdi,
                    })}
                  </p>
                ) : null}
                <p className={appStyles.hint}>{t("rateNote")}</p>
                <label className={s.check}>
                  <input
                    type="checkbox"
                    checked={rateConfirmed}
                    disabled={!shownPreview}
                    onChange={(event) => setRateConfirmed(event.target.checked)}
                  />
                  <span>{t("rateConfirm")}</span>
                </label>
                {submitted && !rateConfirmed ? (
                  <span className={appStyles.error}>{t("rateRequired")}</span>
                ) : null}
              </div>
            </>
          ) : null}

          <div className={appStyles.fieldRow}>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-date`}>
                {t("date")}
              </label>
              <input
                id={`${id}-date`}
                type="date"
                className={appStyles.input}
                value={date}
                max={today}
                onChange={(event) => setDate(event.target.value || today)}
              />
            </div>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-note`}>
                {t("note")}
              </label>
              <input
                id={`${id}-note`}
                className={appStyles.input}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                maxLength={200}
                dir="auto"
                placeholder={common("optional")}
              />
            </div>
          </div>
          {failure ? <Notice tone="error">{failure}</Notice> : null}
        </form>
      )}
    </AccountDialog>
  );
}
