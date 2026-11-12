"use client";

import { CircleCheck, CircleDashed, CircleX, Clock, Undo2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import { fxApi } from "@/lib/fx";
import { type Member, type Settlement, settlementsApi, type Trip } from "@/lib/trips";
import { AccountDialog, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { useFinanceError } from "../finance/parts";
import { MoneyInput } from "../money-input";
import { Button, Notice } from "../ui";
import { isolate } from "./parts";
import s from "./trips.module.css";
import { Select } from "../select";
import { useConfirm } from "../confirm-dialog";

/**
 * Payments between members, as they report them. FinPilot moves no money: "I paid Bob €25" is
 * recorded by the payer, and counts only once Bob confirms he received it (FINANCIAL_RULES §13).
 */

export function SettlementStatusTag({ settlement }: { settlement: Settlement }) {
  const t = useTranslations("app.trips.payments.status");
  const tone: Record<Settlement["status"], { icon: typeof Clock; tone?: string }> = {
    pending: { icon: Clock, tone: "accent" },
    confirmed: { icon: CircleCheck, tone: "positive" },
    rejected: { icon: CircleX, tone: "negative" },
    cancelled: { icon: CircleDashed },
    reversal_pending: { icon: Undo2, tone: "accent" },
    reversed: { icon: Undo2 },
  };
  const { icon: Icon, tone: value } = tone[settlement.status];
  return (
    <span className={s.tag} data-tone={value}>
      <Icon aria-hidden="true" />
      {settlement.kind === "reversal" ? t(`reversal_${settlement.state}`) : t(settlement.status)}
    </span>
  );
}

/** "Alice paid Bob €25", in the reader's language, names isolated for right-to-left text. */
export function usePaymentLine() {
  const t = useTranslations("app.trips.payments");
  const locale = useLocale() as Locale;
  const user = useUser();
  return (x: Settlement) => {
    const amount = formatMoney(x.amount, locale);
    const payer = x.payer.user_id === user.id ? t("you") : isolate(x.payer.display_name);
    const recipient =
      x.recipient.user_id === user.id ? t("youObject") : isolate(x.recipient.display_name);
    if (x.kind === "reversal") {
      const from = x.payer.user_id === user.id ? t("youName") : isolate(x.payer.display_name);
      const to = x.recipient.user_id === user.id ? t("youName") : isolate(x.recipient.display_name);
      return t("reversalLine", { payer: from, recipient: to, amount: isolate(amount) });
    }
    return t("line", { payer, recipient, amount: isolate(amount) });
  };
}

export function SettleDialog({
  trip,
  members,
  open,
  onClose,
  onSaved,
  prefill,
}: {
  trip: Trip;
  members: Member[];
  open: boolean;
  onClose: () => void;
  onSaved: (saved: Settlement & { warnings: string[] }) => void;
  prefill?: { recipient: string; amount: string; corrects?: string | null } | null;
}) {
  const t = useTranslations("app.trips.payments.form");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { currencies } = useFinance();
  const message = useFinanceError();
  const id = useId();
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const { accounts } = useFinance();
  const others = members.filter((m) => m.user_id !== user.id);
  // Paid in another currency: what was actually paid, and the trip amount it settles.
  const [paidIn, setPaidIn] = useState(trip.base_currency);
  const converted = paidIn !== trip.base_currency;
  const exponent = exponentOf(paidIn);
  const [settles, setSettles] = useState("");
  const [reference, setReference] = useState<{ rateId: string; text: string; base: string } | null>(
    null,
  );
  const currencyChoices = [
    ...new Set([trip.base_currency, ...accounts.map((a) => a.currency), "USD", "EUR", "TRY"]),
  ];
  const [recipient, setRecipient] = useState(prefill?.recipient ?? others[0]?.user_id ?? "");
  const [amount, setAmount] = useState(prefill ? editableAmount(prefill.amount, locale) : "");
  const [date, setDate] = useState(todayIn(user.time_zone));
  const [note, setNote] = useState("");
  const [duplicate, setDuplicate] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);
  const parsed = amount.trim() ? parseAmount(amount, locale, exponent) : null;
  const parsedSettles = settles.trim()
    ? parseAmount(settles, locale, exponentOf(trip.base_currency))
    : null;
  const name = members.find((m) => m.user_id === recipient)?.display_name ?? "";

  // The stored reference rate for the day, as a starting point; never invented.
  const lookUp = async () => {
    if (!parsed?.ok) return;
    try {
      const result = await fxApi.convert({
        from: paidIn,
        to: trip.base_currency,
        amount: parsed.amount,
        date,
      });
      if (result.converted && result.rate) {
        setSettles(editableAmount(result.converted.amount, locale));
        setReference({
          rateId: result.rate.id,
          base: result.converted.amount,
          text: t("referenceLine", {
            rate: `1 ${result.rate.base} = ${result.rate.rate} ${result.rate.quote}`,
            date: formatDate(`${result.rate.rate_date}T12:00:00Z`, locale, "UTC"),
          }),
        });
      } else setReference(null);
    } catch {
      setReference(null);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!recipient || !parsed?.ok) return;
    if (converted && !parsedSettles?.ok) return;
    // Unchanged reference: send the rate row. Edited or typed: send the trip amount it settles.
    const useReference =
      converted && reference && parsedSettles?.ok && parsedSettles.amount === reference.base;
    setBusy(true);
    try {
      const saved = await settlementsApi.record(
        trip.id,
        {
          recipient_user_id: recipient,
          amount: { amount: parsed.amount, currency: paidIn },
          ...(converted
            ? useReference && reference
              ? { rate: { rate_id: reference.rateId } }
              : { base_amount: parsedSettles?.ok ? parsedSettles.amount : null }
            : {}),
          paid_on: date,
          note: note.trim() || null,
          corrects_settlement_id: prefill?.corrects ?? null,
          confirm_duplicate: duplicate,
        },
        key,
      );
      onSaved(saved);
    } catch (error) {
      if (handleAuthError(error)) return;
      if (error instanceof ApiError && error.code === "possible_duplicate") {
        setDuplicate(true);
        setKey(newIdempotencyKey());
        setFailure(t("duplicate"));
      } else setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={prefill?.corrects ? t("correctTitle") : t("title")}
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {duplicate ? t("saveAnyway") : t("save")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone={duplicate ? "info" : "error"}>{failure}</Notice> : null}
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-to`}>
            {t("to")}
          </label>
          <Select
            id={`${id}-to`}
            value={recipient}
            disabled={!!prefill?.corrects}
            onValueChange={setRecipient}
          >
            {others.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.display_name}
              </option>
            ))}
          </Select>
        </div>
        {!prefill?.corrects ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-paid-in`}>
              {t("paidIn")}
            </label>
            <Select
              id={`${id}-paid-in`}
              value={paidIn}
              dir="ltr"
              onValueChange={(value) => {
                setPaidIn(value);
                setReference(null);
                setSettles("");
              }}
            >
              {currencyChoices.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {t("amount", { currency: paidIn })}
          </label>
          <MoneyInput
            id={`${id}-amount`}
            className={appStyles.input}
            value={amount}
            onValueChange={setAmount}
            aria-invalid={(parsed && !parsed.ok) || (submitted && !parsed) ? true : undefined}
          />
          {parsed && !parsed.ok ? (
            <span className={appStyles.error}>
              {amountT(`errors.${parsed.error}`, {
                example: formatMoney({ amount: "1250.5", currency: trip.base_currency }, locale, {
                  display: "none",
                }),
                currency: trip.base_currency,
                count: exponent,
              })}
            </span>
          ) : submitted && !parsed ? (
            <span className={appStyles.error}>{t("amountRequired")}</span>
          ) : (
            <span className={appStyles.hint}>{t("partialHint")}</span>
          )}
        </div>
        {converted ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-settles`}>
              {t("settles", { currency: trip.base_currency })}
            </label>
            <MoneyInput
              id={`${id}-settles`}
              className={appStyles.input}
              value={settles}
              onValueChange={setSettles}
              aria-invalid={submitted && !parsedSettles?.ok ? true : undefined}
            />
            <span className={appStyles.hint}>
              {reference && parsedSettles?.ok && parsedSettles.amount === reference.base
                ? reference.text
                : t("settlesHint", { currency: trip.base_currency })}{" "}
              <button
                type="button"
                className={appStyles.linkBtn}
                onClick={() => void lookUp()}
                disabled={!parsed?.ok}
              >
                {t("useReference")}
              </button>
            </span>
            {submitted && !parsedSettles?.ok ? (
              <span className={appStyles.error}>{t("settlesRequired")}</span>
            ) : null}
          </div>
        ) : null}
        <div className={fs.fieldsRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-date`}>
              {t("date")}
            </label>
            <input
              id={`${id}-date`}
              type="date"
              className={appStyles.input}
              value={date}
              max={todayIn(user.time_zone)}
              onChange={(e) => setDate(e.target.value)}
            />
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-note`}>
              {t("note")}
            </label>
            <input
              id={`${id}-note`}
              className={appStyles.input}
              dir="auto"
              maxLength={200}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
        </div>
        <p className={s.notice}>{t("confirmNotice", { name: isolate(name) })}</p>
      </form>
    </AccountDialog>
  );
}

/** One payment with the actions this person may take. */
export function PaymentRow({
  trip,
  settlement,
  onChanged,
  onCorrect,
  answeredAbove = false,
}: {
  trip: Trip;
  settlement: Settlement;
  onChanged: (notice: string) => void;
  onCorrect?: (settlement: Settlement) => void;
  /** Shown in the history while it also waits for you under "Waiting for you": answer it there,
   *  so the confirm and reject buttons exist in one place only. */
  answeredAbove?: boolean;
}) {
  const t = useTranslations("app.trips.payments");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const line = usePaymentLine();
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const x = settlement;
  const can = answeredAbove ? { ...x.can, confirm: false, reject: false } : x.can;

  const act = async (action: "confirm" | "reject" | "cancel" | "reverse") => {
    if (action === "reverse" && !(await ask({ title: t("reverseConfirm") }))) return;
    setBusy(action);
    setFailure(null);
    try {
      await settlementsApi.act(trip.id, x.id, action, newIdempotencyKey(), reason);
      onChanged(t(`done.${action}`));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(null);
    }
  };

  const [confirmEl, ask] = useConfirm();
  return (
    <li className={s.payment}>
      {confirmEl}
      <div className={s.paymentTop}>
        <span className={s.paymentLine}>{line(x)}</span>
        <SettlementStatusTag settlement={x} />
      </div>
      <span className={s.rowSub}>
        {formatDate(`${x.paid_on}T12:00:00Z`, locale, "UTC")}
        {x.base_amount && x.base_amount.currency !== x.amount.currency ? (
          <>
            {" · "}
            {t("countsAs", { amount: isolate(formatMoney(x.base_amount, locale)) })}
          </>
        ) : null}
        {x.note ? (
          <>
            {" · "}
            <span dir="auto">{x.note}</span>
          </>
        ) : null}
        {x.decision_reason ? (
          <>
            {" · “"}
            <span dir="auto">{x.decision_reason}</span>
            {"”"}
          </>
        ) : null}
      </span>
      {answeredAbove ? <span className={s.note}>{t("answerAbove")}</span> : null}
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      {can.confirm || can.cancel || can.reverse ? (
        <div className={s.paymentActions}>
          {can.confirm ? (
            <Button
              size="sm"
              variant="primary"
              busy={busy === "confirm"}
              onClick={() => act("confirm")}
            >
              {x.kind === "reversal" ? t("confirmReversal") : t("confirmReceived")}
            </Button>
          ) : null}
          {can.reject && !rejecting ? (
            <Button size="sm" onClick={() => setRejecting(true)}>
              {t("reject")}
            </Button>
          ) : null}
          {can.cancel ? (
            <Button
              size="sm"
              variant="ghost"
              busy={busy === "cancel"}
              onClick={() => act("cancel")}
            >
              {t("cancel")}
            </Button>
          ) : null}
          {can.reverse ? (
            <Button
              size="sm"
              variant="ghost"
              busy={busy === "reverse"}
              onClick={() => act("reverse")}
            >
              {t("reverse")}
            </Button>
          ) : null}
          {onCorrect && x.status === "reversed" && x.kind === "payment" ? (
            <Button size="sm" variant="ghost" onClick={() => onCorrect(x)}>
              {t("correct")}
            </Button>
          ) : null}
        </div>
      ) : onCorrect && x.status === "reversed" && x.kind === "payment" ? (
        <div className={s.paymentActions}>
          <Button size="sm" variant="ghost" onClick={() => onCorrect(x)}>
            {t("correct")}
          </Button>
        </div>
      ) : null}
      {rejecting ? (
        <div className={s.paymentActions}>
          <input
            className={appStyles.input}
            aria-label={t("reason")}
            placeholder={t("reason")}
            dir="auto"
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <Button size="sm" variant="danger" busy={busy === "reject"} onClick={() => act("reject")}>
            {t("confirmReject")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>
            {t("keep")}
          </Button>
        </div>
      ) : null}
    </li>
  );
}

export function PaymentsCard({
  trip,
  settlements,
  onChanged,
  onCorrect,
  waitingIds = new Set(),
}: {
  trip: Trip;
  settlements: Settlement[];
  onChanged: (notice: string) => void;
  onCorrect: (settlement: Settlement) => void;
  /** Payments already listed under "Waiting for you". */
  waitingIds?: ReadonlySet<string>;
}) {
  const t = useTranslations("app.trips.payments");
  return (
    <section className={s.card} aria-labelledby="payments-title">
      <div className={s.cardHead}>
        <h2 id="payments-title" className={s.cardTitle}>
          {t("title")}
        </h2>
        <span className={s.note}>{t("count", { count: settlements.length })}</span>
      </div>
      <div className={s.cardBody}>
        {settlements.length === 0 ? (
          <p className={s.note}>{t("empty")}</p>
        ) : (
          <ul className={s.payments}>
            {settlements.map((x) => (
              <PaymentRow
                key={x.id}
                trip={trip}
                settlement={x}
                onChanged={onChanged}
                onCorrect={onCorrect}
                answeredAbove={waitingIds.has(x.id)}
              />
            ))}
          </ul>
        )}
        <p className={s.note} style={{ marginBlockStart: 8 }}>
          {t("explainer")}
        </p>
      </div>
    </section>
  );
}
