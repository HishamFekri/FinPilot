"use client";

import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import {
  type Account,
  type AdjustmentPreview,
  accountsApi,
  type CurrencyInfo,
  newIdempotencyKey,
} from "@/lib/accounts";
import { formatMoney, parseAmount } from "@/lib/money";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { bdi, Button, Notice } from "../ui";
import { useAccountName } from "../finance/parts";
import s from "./accounts.module.css";
import { AccountDialog, AmountField, Money, useErrorMessage } from "./parts";
import { Select } from "../select";

/**
 * "My wallet actually holds 40, FinPilot says 55." The person enters the real balance; the
 * backend works out the difference (M5) and, after they've reviewed it, posts it as an adjustment
 * against Equity:Adjustment. Nothing earlier is edited.
 */
export function AdjustDialog({
  accounts,
  initialAccountId,
  currencies,
  open,
  onOpenChange,
  onAdjusted,
}: {
  accounts: Account[];
  initialAccountId?: string;
  currencies: CurrencyInfo[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdjusted: (account: Account) => void;
}) {
  const t = useTranslations("app.accounts.adjust");
  const common = useTranslations("app.accounts.form");
  const accountName = useAccountName();
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useErrorMessage();
  const id = useId();

  const [accountId, setAccountId] = useState(initialAccountId ?? accounts[0]?.id ?? "");
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [preview, setPreview] = useState<AdjustmentPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);

  const account = accounts.find((a) => a.id === accountId);
  const exponent = currencies.find((c) => c.code === account?.currency)?.exponent ?? 2;

  const reset = (nextAccount = initialAccountId ?? accounts[0]?.id ?? "") => {
    setAccountId(nextAccount);
    setTarget("");
    setReason("");
    setSubmitted(false);
    setPreview(null);
    setFailure(null);
    setKey(newIdempotencyKey());
  };

  const review = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!account) return;
    const parsed = parseAmount(target, locale, exponent, { allowNegative: true });
    if (!parsed.ok) return;
    setBusy(true);
    try {
      const result = await accountsApi.previewAdjustment(account.id, {
        amount: parsed.amount,
        currency: account.currency,
      });
      if (result.direction === "none") setFailure(t("noChange"));
      else setPreview(result);
    } catch (error) {
      if (handleAuthError(error)) return;
      setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!account || !preview) return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await accountsApi.adjust(
        account.id,
        {
          target_balance: preview.target,
          // What was shown. If anything was recorded since, the API refuses (409).
          expected_balance: preview.current,
          reason: reason.trim() || null,
        },
        key,
      );
      onAdjusted(result.account);
      onOpenChange(false);
      reset();
    } catch (error) {
      if (handleAuthError(error)) return;
      setFailure(message(error));
      if (error instanceof ApiError && error.kind !== "unavailable") {
        setPreview(null);
        setKey(newIdempotencyKey());
      }
    } finally {
      setBusy(false);
    }
  };

  const footer = preview ? (
    <>
      <Button variant="ghost" onClick={() => setPreview(null)} disabled={busy}>
        {t("back")}
      </Button>
      <Button variant="primary" busy={busy} onClick={() => void confirm()}>
        {t("confirm")}
      </Button>
    </>
  ) : (
    <>
      <Button variant="ghost" onClick={() => onOpenChange(false)}>
        {common("cancel")}
      </Button>
      <Button type="submit" form={`${id}-form`} variant="primary" busy={busy} disabled={!account}>
        {t("review")}
      </Button>
    </>
  );

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (next) reset();
        onOpenChange(next);
      }}
      title={preview ? t("reviewTitle") : t("title")}
      lead={preview ? undefined : t("lead")}
      footer={footer}
    >
      {preview && account ? (
        <>
          <dl className={s.review}>
            <div className={s.reviewRow}>
              <dt>{t("recorded")}</dt>
              <dd>
                <Money value={preview.current} />
              </dd>
            </div>
            <div className={s.reviewRow}>
              <dt>{t("actual")}</dt>
              <dd>
                <Money value={preview.target} />
              </dd>
            </div>
            <div className={`${s.reviewRow} ${s.reviewStrong}`}>
              <dt>{t("difference")}</dt>
              <dd>
                <Money
                  value={{
                    amount:
                      (preview.direction === "decrease" ? "-" : "") + preview.difference.amount,
                    currency: preview.difference.currency,
                  }}
                  signed
                />
              </dd>
            </div>
          </dl>
          <p>
            {t.rich(preview.direction === "increase" ? "willIncrease" : "willDecrease", {
              amount: formatMoney(preview.difference, locale),
              name: accountName(account),
              bdi,
            })}
          </p>
          <p className={appStyles.hint}>{t("notIncome")}</p>
          {reason.trim() ? (
            <p className={appStyles.hint}>{t.rich("withReason", { reason: reason.trim(), bdi })}</p>
          ) : null}
          {failure ? <Notice tone="error">{failure}</Notice> : null}
        </>
      ) : (
        <form id={`${id}-form`} className={appStyles.form} onSubmit={review} noValidate>
          {initialAccountId ? null : (
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-account`}>
                {t("account")}
              </label>
              <Select
                id={`${id}-account`}
                value={accountId}
                onValueChange={(value) => {
                  setAccountId(value);
                  setTarget("");
                }}
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {`${accountName(a)} · ${formatMoney(a.balance, locale, { display: "code" })}`}
                  </option>
                ))}
              </Select>
            </div>
          )}
          {account ? (
            <dl className={s.review}>
              <div className={s.reviewRow}>
                <dt>{t("recorded")}</dt>
                <dd>
                  <Money value={account.balance} />
                </dd>
              </div>
            </dl>
          ) : null}
          {account ? (
            <AmountField
              label={t("actual")}
              currency={account.currency}
              exponent={exponent}
              value={target}
              onChange={setTarget}
              allowNegative
              showErrors={submitted}
            />
          ) : null}
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-reason`}>
              {t("reason")}
            </label>
            <input
              id={`${id}-reason`}
              className={appStyles.input}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={500}
              dir="auto"
              aria-describedby={`${id}-reason-hint`}
            />
            <span id={`${id}-reason-hint`} className={appStyles.hint}>
              {t("reasonHint")}
            </span>
          </div>
          {failure ? <Notice tone="error">{failure}</Notice> : null}
        </form>
      )}
    </AccountDialog>
  );
}
