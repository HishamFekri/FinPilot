"use client";

import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { linksApi } from "@/lib/links";
import { formatDate } from "@/lib/format";
import { fxApi, type RateView } from "@/lib/fx";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import {
  type Expense,
  type ExpenseContent,
  type Member,
  type Preview,
  type SplitMethod,
  TRIP_CATEGORIES,
  type Trip,
  tripsApi,
} from "@/lib/trips";
import { AccountDialog, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { useAccountName, useFinanceError } from "../finance/parts";
import { MoneyInput } from "../money-input";
import { Button, Notice } from "../ui";
import { useTripCategory } from "./parts";
import s from "./trips.module.css";
import { Select } from "../select";

/**
 * Record a shared expense, or propose a correction to one. Shares are worked out by the API (the
 * preview); nothing here does money arithmetic. If the payer isn't you, the payer confirms it
 * before it counts (FINANCIAL_RULES.md §12.1).
 */
export function ExpenseDialog({
  trip,
  members,
  expense,
  open,
  onClose,
  onSaved,
}: {
  trip: Trip;
  members: Member[];
  /** Set to propose a correction of this expense's current version. */
  expense?: Expense | null;
  open: boolean;
  onClose: () => void;
  onSaved: (saved: Expense) => void;
}) {
  const t = useTranslations("app.trips.expense");
  const amountT = useTranslations("app.accounts.amount");
  const fxT = useTranslations("app.fx");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { currencies } = useFinance();
  const message = useFinanceError();
  const categoryName = useTripCategory();
  const id = useId();
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const base = expense?.current_version ?? null;

  const kept = new Set(base?.shares.map((x) => x.user_id) ?? []);
  const eligible = members.filter((m) => m.status === "active" || kept.has(m.user_id));
  const active = members.filter((m) => m.status === "active");

  const [payer, setPayer] = useState(expense?.payer.user_id ?? user.id);
  // One step (private): the payer may record their own cash for it now. Never shown to others.
  const { accounts } = useFinance();
  const accountName = useAccountName();
  const ownAccounts = accounts.filter((a) => a.status === "active");
  const [record, setRecord] = useState(false);
  const [recordAccount, setRecordAccount] = useState(
    ownAccounts.find((a) => a.currency === trip.base_currency)?.id ?? ownAccounts[0]?.id ?? "",
  );
  const [recordAmount, setRecordAmount] = useState("");
  const [description, setDescription] = useState(base?.description ?? "");
  const [amount, setAmount] = useState(base ? editableAmount(base.amount.amount, locale) : "");
  const [currency, setCurrency] = useState(base?.amount.currency ?? trip.base_currency);
  const [date, setDate] = useState(base?.expense_date ?? todayIn(user.time_zone));
  const [category, setCategory] = useState(base?.category_key ?? "food_and_drinks");
  const [method, setMethod] = useState<SplitMethod>(base?.split_method ?? "equal");
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(base ? base.shares.map((x) => x.user_id) : active.map((m) => m.user_id)),
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      (base?.shares ?? []).map((x) => [
        x.user_id,
        x.input_value
          ? base?.split_method === "fixed"
            ? editableAmount(x.input_value, locale)
            : x.input_value
          : "",
      ]),
    ),
  );
  const [notes, setNotes] = useState(base?.notes ?? "");
  const [rate, setRate] = useState<{
    for: string;
    value?: RateView | null;
    failed?: boolean;
  } | null>(null);
  const [useManual, setUseManual] = useState(base?.rate?.is_manual ?? false);
  const [manual, setManual] = useState(base?.rate?.is_manual ? base.rate.rate : "");
  const [staleOk, setStaleOk] = useState(false);
  const [preview, setPreview] = useState<{ for: string; value?: Preview; error?: string } | null>(
    null,
  );
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);

  const converted = currency !== trip.base_currency;
  const parsed = amount.trim() ? parseAmount(amount, locale, exponentOf(currency)) : null;
  const chosen = eligible.filter((m) => selected.has(m.user_id));
  const rateKey = `${currency}|${date}`;

  // The reference rate for the expense's currency and date (stored by the API, display only).
  useEffect(() => {
    if (!converted) return;
    let cancelled = false;
    fxApi
      .convert({ from: currency, to: trip.base_currency, date })
      .then((result) => {
        if (!cancelled) setRate({ for: rateKey, value: result.rate });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setRate({ for: rateKey, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [converted, currency, date, trip.base_currency, rateKey, handleAuthError]);

  const reference = rate?.for === rateKey ? (rate.value ?? null) : null;
  const rateFailed = rate?.for === rateKey && (rate.failed || rate.value === null);
  const manualMode = converted && (useManual || rateFailed);

  const content = (): ExpenseContent | null => {
    if (!parsed?.ok || chosen.length === 0) return null;
    const shares = chosen.map((m) => {
      if (method === "equal") return { user_id: m.user_id };
      const raw = values[m.user_id] ?? "";
      if (method === "fixed") {
        const v = parseAmount(raw, locale, exponentOf(currency));
        return { user_id: m.user_id, value: v.ok ? v.amount : "x" };
      }
      return { user_id: m.user_id, value: raw.replace(",", ".").trim() };
    });
    let rateChoice: ExpenseContent["rate"] = null;
    if (converted) {
      if (manualMode) {
        if (!manual.trim()) return null;
        rateChoice = { manual: manual.replace(",", ".").trim() };
      } else if (reference) {
        rateChoice = { rate_id: reference.id };
      } else return null;
    }
    return {
      description: description.trim() || "-",
      category_key: category,
      expense_date: date,
      amount: { amount: parsed.amount, currency },
      split: { method, shares },
      rate: rateChoice,
      acknowledge_stale_rate: staleOk,
      notes: notes.trim() || null,
    };
  };
  const draft = content();
  const previewKey = draft ? JSON.stringify([draft, payer]) : "";

  // Live shares from the API, a moment after typing stops.
  useEffect(() => {
    if (!draft) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      tripsApi
        .preview(trip.id, { ...draft, payer_user_id: payer })
        .then((value) => {
          if (!cancelled) setPreview({ for: previewKey, value });
        })
        .catch((error: unknown) => {
          if (!cancelled && !handleAuthError(error))
            setPreview({ for: previewKey, error: message(error) });
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // previewKey captures the draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey, trip.id, handleAuthError]);

  const current = preview?.for === previewKey ? preview : null;
  const shareOf = (userId: string) => current?.value?.shares.find((x) => x.user_id === userId);
  const personal = chosen.length === 1 && chosen[0]?.user_id === payer;
  const stale = reference && reference.is_stale && !manualMode;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!description.trim() || !draft || current?.error || (stale && !staleOk)) return;
    const linking = record && !expense && payer === user.id;
    const chargedText = recordAmount.trim() || (sameCurrency ? amount : "");
    const charged =
      linking && chargedText.trim()
        ? parseAmount(chargedText, locale, exponentOf(recordCurrency))
        : null;
    if (linking && (!recordAccount || !charged?.ok)) {
      setFailure(t("recordIncomplete"));
      return;
    }
    setBusy(true);
    try {
      const saved = expense
        ? await tripsApi.correct(trip.id, expense.id, draft, key)
        : linking && charged?.ok
          ? (
              await linksApi.expenseWithLink(
                trip.id,
                { ...draft, payer_user_id: payer },
                { account_id: recordAccount, amount: charged.amount, effective_date: date },
                key,
              )
            ).expense
          : await tripsApi.record(trip.id, { ...draft, payer_user_id: payer }, key);
      onSaved(saved);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (userId: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  const payerName = members.find((m) => m.user_id === payer)?.display_name ?? "";
  const recordCurrency = ownAccounts.find((a) => a.id === recordAccount)?.currency ?? currency;
  const sameCurrency = recordCurrency === currency;

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={expense ? t("correctTitle") : t("title")}
      lead={expense ? t("correctLead") : t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {payer === user.id ? t("save") : t("propose", { name: payerName })}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone="error">{failure}</Notice> : null}

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-desc`}>
            {t("description")}
          </label>
          <input
            id={`${id}-desc`}
            className={appStyles.input}
            dir="auto"
            maxLength={120}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            aria-invalid={submitted && !description.trim() ? true : undefined}
          />
          {submitted && !description.trim() ? (
            <span className={appStyles.error}>{t("descriptionRequired")}</span>
          ) : null}
        </div>

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {t("amount")}
          </label>
          <div className={fs.inlineMoney}>
            <Select
              size="sm"
              className={fs.currencySelect}
              aria-label={t("currency")}
              value={currency}
              onValueChange={setCurrency}
            >
              {[...new Set([trip.base_currency, currency, "EUR", "USD", "GBP", "TRY"])].map(
                (code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ),
              )}
            </Select>
            <MoneyInput
              id={`${id}-amount`}
              className={appStyles.input}
              value={amount}
              onValueChange={setAmount}
              aria-invalid={(parsed && !parsed.ok) || (submitted && !parsed) ? true : undefined}
            />
          </div>
          {parsed && !parsed.ok ? (
            <span className={appStyles.error}>
              {amountT(`errors.${parsed.error}`, {
                example: formatMoney({ amount: "1250.5", currency }, locale, { display: "none" }),
                currency,
                count: exponentOf(currency),
              })}
            </span>
          ) : submitted && !parsed ? (
            <span className={appStyles.error}>{t("amountRequired")}</span>
          ) : null}
        </div>

        {converted ? (
          <div className={s.rateBox}>
            {reference && !manualMode ? (
              <>
                <span>
                  <bdi dir="ltr">
                    {fxT("rateLine", {
                      from: currency,
                      rate: reference.rate,
                      to: trip.base_currency,
                    })}
                  </bdi>
                  {" · "}
                  {reference.upstream === "ECB"
                    ? fxT("sourceEcb", {
                        date: formatDate(`${reference.rate_date}T12:00:00Z`, locale, "UTC"),
                      })
                    : fxT("sourceBlended", {
                        date: formatDate(`${reference.rate_date}T12:00:00Z`, locale, "UTC"),
                      })}
                </span>
                {stale ? (
                  <label className={fs.checkRow}>
                    <input
                      type="checkbox"
                      checked={staleOk}
                      onChange={(e) => setStaleOk(e.target.checked)}
                    />
                    <span>{fxT("staleAccept")}</span>
                  </label>
                ) : null}
                <button
                  type="button"
                  className={appStyles.linkBtn}
                  onClick={() => setUseManual(true)}
                >
                  {t("ownRate")}
                </button>
              </>
            ) : (
              <>
                {rateFailed ? <span>{t("noRate")}</span> : null}
                <label className={appStyles.label} htmlFor={`${id}-rate`}>
                  {t("manualRate", { from: currency, to: trip.base_currency })}
                </label>
                <input
                  id={`${id}-rate`}
                  className={appStyles.input}
                  inputMode="decimal"
                  dir="ltr"
                  maxLength={20}
                  value={manual}
                  onChange={(e) => setManual(e.target.value)}
                />
                <span className={appStyles.hint}>{t("manualHint")}</span>
                {reference ? (
                  <button
                    type="button"
                    className={appStyles.linkBtn}
                    onClick={() => setUseManual(false)}
                  >
                    {t("useReference")}
                  </button>
                ) : null}
              </>
            )}
          </div>
        ) : null}

        <div className={fs.fieldsRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-payer`}>
              {t("payer")}
            </label>
            <Select id={`${id}-payer`} value={payer} disabled={!!expense} onValueChange={setPayer}>
              {(expense ? members.filter((m) => m.user_id === payer) : active).map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.user_id === user.id ? t("you", { name: m.display_name }) : m.display_name}
                </option>
              ))}
            </Select>
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-date`}>
              {t("date")}
            </label>
            <input
              id={`${id}-date`}
              type="date"
              className={appStyles.input}
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </div>
        </div>
        {payer !== user.id ? (
          <p className={s.notice}>{t("needsConfirmation", { name: payerName })}</p>
        ) : null}
        {!expense && payer === user.id && ownAccounts.length > 0 ? (
          <div className={s.recordBox}>
            <label className={s.option}>
              <input
                type="checkbox"
                checked={record}
                onChange={(e) => setRecord(e.target.checked)}
              />
              <span>
                <strong>{t("recordToggle")}</strong>
                <span className={s.note}> {t("recordHint")}</span>
              </span>
            </label>
            {record ? (
              <div className={fs.fieldsRow}>
                <div className={appStyles.field}>
                  <label className={appStyles.label} htmlFor={`${id}-rec-account`}>
                    {t("recordAccount")}
                  </label>
                  <Select
                    id={`${id}-rec-account`}
                    value={recordAccount}
                    onValueChange={setRecordAccount}
                  >
                    {ownAccounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {`${accountName(a)} · ${a.currency}`}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className={appStyles.field}>
                  <label className={appStyles.label} htmlFor={`${id}-rec-amount`}>
                    {t("recordAmount", { currency: recordCurrency })}
                  </label>
                  <MoneyInput
                    id={`${id}-rec-amount`}
                    className={appStyles.input}
                    value={recordAmount}
                    placeholder={sameCurrency ? amount : ""}
                    onValueChange={setRecordAmount}
                  />
                  <span className={appStyles.hint}>
                    {sameCurrency ? t("recordSameHint") : t("recordOtherHint")}
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
        {expense ? <p className={appStyles.hint}>{t("payerFixed")}</p> : null}

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-cat`}>
            {t("category")}
          </label>
          <Select id={`${id}-cat`} value={category} onValueChange={setCategory}>
            {TRIP_CATEGORIES.map((key) => (
              <option key={key} value={key}>
                {categoryName(key)}
              </option>
            ))}
          </Select>
        </div>

        <div className={appStyles.field}>
          <span className={appStyles.label}>{t("split")}</span>
          <div className={fs.segmented} role="group" aria-label={t("split")}>
            {(["equal", "fixed", "percentage"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={fs.segment}
                aria-pressed={method === m}
                onClick={() => setMethod(m)}
              >
                {t(`methods.${m}`)}
              </button>
            ))}
          </div>
        </div>

        <fieldset className={s.choices}>
          <legend className={appStyles.label}>{t("forWhom")}</legend>
          {eligible.map((m) => {
            const on = selected.has(m.user_id);
            const share = shareOf(m.user_id);
            return (
              <div key={m.user_id} className={s.choiceRow}>
                <input
                  id={`${id}-m-${m.user_id}`}
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(m.user_id)}
                />
                <label htmlFor={`${id}-m-${m.user_id}`} className={s.choiceName} dir="auto">
                  {m.display_name}
                  {m.status !== "active" ? ` · ${t("former")}` : ""}
                </label>
                {method === "equal" || !on ? (
                  <span />
                ) : method === "fixed" ? (
                  <MoneyInput
                    className={`${appStyles.input} ${s.smallInput}`}
                    aria-label={t("amountFor", { name: m.display_name })}
                    value={values[m.user_id] ?? ""}
                    onValueChange={(v) => setValues((prev) => ({ ...prev, [m.user_id]: v }))}
                  />
                ) : (
                  <input
                    className={`${appStyles.input} ${s.smallInput}`}
                    aria-label={t("percentFor", { name: m.display_name })}
                    inputMode="decimal"
                    dir="ltr"
                    maxLength={8}
                    value={values[m.user_id] ?? ""}
                    onChange={(e) =>
                      setValues((prev) => ({ ...prev, [m.user_id]: e.target.value }))
                    }
                  />
                )}
                <span className={s.choiceShare}>
                  {on && share ? <bdi dir="ltr">{formatMoney(share.base_share, locale)}</bdi> : ""}
                </span>
              </div>
            );
          })}
          {submitted && chosen.length === 0 ? (
            <span className={appStyles.error}>{t("chooseSomeone")}</span>
          ) : null}
        </fieldset>

        {current?.value ? (
          <div className={s.totalsLine} aria-live="polite">
            <span>{t(`classes.${current.value.classification}`)}</span>
            <span>
              {converted ? (
                <bdi dir="ltr">
                  {`${formatMoney(current.value.amount, locale)} → ${formatMoney(current.value.base_amount, locale)}`}
                </bdi>
              ) : (
                <bdi dir="ltr">{formatMoney(current.value.base_amount, locale)}</bdi>
              )}
            </span>
          </div>
        ) : null}
        {current?.error ? <span className={appStyles.error}>{current.error}</span> : null}
        {personal ? <p className={s.notice}>{t("personalNotice")}</p> : null}

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-notes`}>
            {t("notes")}
          </label>
          <textarea
            id={`${id}-notes`}
            className={appStyles.input}
            dir="auto"
            rows={2}
            maxLength={500}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
      </form>
    </AccountDialog>
  );
}
