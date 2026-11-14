"use client";

import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, Fragment, useEffect, useId, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/accounts";
import { templatesApi } from "@/lib/everyday";
import { financeApi, type TransactionDetail } from "@/lib/finance";
import { formatDate, formatDateTime } from "@/lib/format";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import { AccountDialog, AmountField, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { MoneyInput } from "../money-input";
import { bdi, Button, Loading, Notice, Tag } from "../ui";
import { useFinance } from "./finance-provider";
import s from "./finance.module.css";
import {
  CategoryIcon,
  CategoryPicker,
  TxnAmount,
  useAccountName,
  useCategoryName,
  useFinanceError,
} from "./parts";
import { Select } from "../select";

type View = "view" | "correct" | "void" | "refund";

/**
 * One entry, in full: what it was, its history, and (for income and expenses) the two ways to
 * change it. Nothing is ever edited in place or deleted: a correction reverses the original and
 * records the replacement; a void reverses it and leaves it, crossed out, in the history.
 */
export type TxnDialog = {
  target: { id: string; n: number } | null;
  open: boolean;
  show: (id: string) => void;
  close: () => void;
};

/** Which entry the dialog shows. Each opening mounts it fresh, so it always shows current data. */
export function useTxnDialog(): TxnDialog {
  const [target, setTarget] = useState<TxnDialog["target"]>(null);
  const [open, setOpen] = useState(false);
  return {
    target,
    open,
    show: (id) => {
      setTarget((prev) => ({ id, n: (prev?.n ?? 0) + 1 }));
      setOpen(true);
    },
    close: () => setOpen(false),
  };
}

export function TransactionDialog({ dialog }: { dialog: TxnDialog }) {
  if (!dialog.target) return null;
  return (
    <DetailDialog
      key={dialog.target.n}
      txnId={dialog.target.id}
      open={dialog.open}
      onClose={dialog.close}
    />
  );
}

function DetailDialog({
  txnId,
  open,
  onClose,
}: {
  txnId: string;
  open: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("app.transactions.detail");
  const { handleAuthError } = useAuth();
  const { changed } = useFinance();
  const message = useFinanceError();
  const [detail, setDetail] = useState<
    | { kind: "loading" }
    | { kind: "missing" }
    | { kind: "failed" }
    | { kind: "ready"; txn: TransactionDetail }
  >({ kind: "loading" });
  const [view, setView] = useState<View>("view");
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    financeApi
      .get(txnId)
      .then((txn) => {
        if (!cancelled) setDetail({ kind: "ready", txn });
      })
      .catch((error: unknown) => {
        if (cancelled || handleAuthError(error)) return;
        setDetail(
          error instanceof ApiError && error.kind === "not_found"
            ? { kind: "missing" }
            : { kind: "failed" },
        );
      });
    return () => {
      cancelled = true;
    };
  }, [txnId, handleAuthError]);

  const txn = detail.kind === "ready" ? detail.txn : null;
  const correctable =
    txn !== null && txn.status === "posted" && (txn.type === "income" || txn.type === "expense");

  const afterWrite = async (updated: TransactionDetail, text: string) => {
    setDetail({ kind: "ready", txn: updated });
    setView("view");
    setNotice({ tone: "success", text });
    await changed();
  };

  const voidIt = async () => {
    if (!txn) return;
    setBusy(true);
    setNotice(null);
    try {
      await afterWrite(await financeApi.void(txn.id, txn.version), t("voidedDone"));
    } catch (error) {
      if (!handleAuthError(error)) setNotice({ tone: "error", text: message(error) });
    } finally {
      setBusy(false);
    }
  };

  const formId = useId();
  const refundable = txn?.refunds && txn.status === "posted" ? txn.refunds.refundable : null;
  const canRefund = Boolean(refundable && !/^0(\.0+)?$/.test(refundable.amount));
  const saveTemplate = async () => {
    if (!txn) return;
    setBusy(true);
    setNotice(null);
    try {
      await templatesApi.create({ from_transaction_id: txn.id }, newIdempotencyKey());
      setNotice({ tone: "success", text: t("templateSaved") });
    } catch (error) {
      if (!handleAuthError(error)) setNotice({ tone: "error", text: message(error) });
    } finally {
      setBusy(false);
    }
  };
  const title =
    view === "correct"
      ? t("correctTitle")
      : view === "void"
        ? t("voidTitle")
        : view === "refund"
          ? t("refundTitle")
          : t("title");

  const footer =
    view === "refund" ? (
      <>
        <Button variant="ghost" onClick={() => setView("view")} disabled={busy}>
          {t("back")}
        </Button>
        <Button type="submit" form={formId} variant="primary" busy={busy}>
          {t("refundSave")}
        </Button>
      </>
    ) : view === "correct" ? (
      <>
        <Button variant="ghost" onClick={() => setView("view")} disabled={busy}>
          {t("back")}
        </Button>
        <Button type="submit" form={formId} variant="primary" busy={busy}>
          {t("saveCorrection")}
        </Button>
      </>
    ) : view === "void" ? (
      <>
        <Button variant="ghost" onClick={() => setView("view")} disabled={busy}>
          {t("keep")}
        </Button>
        <Button variant="danger" busy={busy} onClick={() => void voidIt()}>
          {t("voidConfirm")}
        </Button>
      </>
    ) : (
      <>
        {correctable ? (
          <>
            <Button variant="ghost" onClick={() => setView("void")}>
              {t("void")}
            </Button>
            <Button variant="ghost" onClick={() => void saveTemplate()} disabled={busy}>
              {t("saveTemplate")}
            </Button>
            <Button onClick={() => setView("correct")}>{t("correct")}</Button>
          </>
        ) : null}
        {canRefund ? <Button onClick={() => setView("refund")}>{t("refund")}</Button> : null}
        <Button variant="primary" onClick={onClose}>
          {t("close")}
        </Button>
      </>
    );

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={title}
      lead={view === "correct" ? t("correctLead") : undefined}
      // Keyed by view: a button that switches view must never be reused as the next view's
      // submit button (the browser would submit the form on the same click).
      footer={<Fragment key={view}>{footer}</Fragment>}
    >
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}
      {detail.kind === "loading" ? (
        <Loading />
      ) : detail.kind === "missing" ? (
        <Notice tone="error">{t("missing")}</Notice>
      ) : detail.kind === "failed" || !txn ? (
        <Notice tone="error">{message(null)}</Notice>
      ) : view === "correct" ? (
        <CorrectForm
          formId={formId}
          txn={txn}
          busy={busy}
          setBusy={setBusy}
          onDone={(updated) => void afterWrite(updated, t("corrected"))}
          onError={(text) => setNotice({ tone: "error", text })}
        />
      ) : view === "void" ? (
        <p className={appStyles.muted}>{t("voidBody")}</p>
      ) : view === "refund" ? (
        <RefundForm
          formId={formId}
          txn={txn}
          busy={busy}
          setBusy={setBusy}
          onDone={(updated) => void afterWrite(updated, t("refunded"))}
          onError={(text) => setNotice({ tone: "error", text })}
        />
      ) : (
        <Facts txn={txn} />
      )}
    </AccountDialog>
  );
}

/**
 * Money back for this purchase (FINANCIAL_RULES §4.11): full or partial, to an account in the
 * purchase's currency, dated when it came back. It reduces spending in that month and the
 * purchase's category, and is never income. Together, refunds never exceed the purchase.
 */
function RefundForm({
  formId,
  txn,
  busy,
  setBusy,
  onDone,
  onError,
}: {
  formId: string;
  txn: TransactionDetail;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onDone: (updated: TransactionDetail) => void;
  onError: (text: string) => void;
}) {
  const t = useTranslations("app.transactions.detail");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, currencies } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const refundable = txn.refunds?.refundable ?? txn.amount;
  const choices = accounts.filter(
    (a) => a.currency === txn.amount.currency && a.status === "active",
  );
  const [accountId, setAccountId] = useState(
    choices.find((a) => a.id === txn.account?.id)?.id ?? choices[0]?.id ?? "",
  );
  // Entered in the receipt's currency when the purchase was: the account amount is then typed.
  const foreign = txn.original_amount ?? null;
  const [inForeign, setInForeign] = useState(false);
  const currency = inForeign && foreign ? foreign.currency : txn.amount.currency;
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const [amount, setAmount] = useState(() => editableAmount(refundable.amount, locale));
  const [charged, setCharged] = useState("");
  const [date, setDate] = useState(() => todayIn(user.time_zone));
  const [description, setDescription] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [key, setKey] = useState(newIdempotencyKey);
  const parsed = amount.trim() ? parseAmount(amount, locale, exponentOf(currency)) : null;
  const parsedCharged = charged.trim()
    ? parseAmount(charged, locale, exponentOf(txn.amount.currency))
    : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setSubmitted(true);
    if (!parsed?.ok || !accountId || !date) return;
    if (inForeign && !parsedCharged?.ok) return;
    setBusy(true);
    try {
      const updated = await financeApi.refund(
        txn.id,
        {
          amount: { amount: parsed.amount, currency },
          account_id: accountId,
          effective_date: date,
          description: description.trim() || null,
          ...(inForeign && parsedCharged?.ok
            ? {
                conversion: {
                  method: "actual_amount" as const,
                  account_amount: parsedCharged.amount,
                },
              }
            : {}),
        },
        key,
      );
      onDone(updated);
    } catch (error) {
      if (handleAuthError(error)) return;
      setKey(newIdempotencyKey());
      onError(
        error instanceof ApiError && error.code === "refund_exceeds_original"
          ? t("refundTooMuch", { amount: formatMoney(refundable, locale) })
          : message(error),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <form id={formId} className={appStyles.form} onSubmit={submit} noValidate>
      <p className={appStyles.muted}>
        {t("refundLead", { amount: formatMoney(refundable, locale) })}
      </p>
      {foreign ? (
        <div className={s.segmented} role="group" aria-label={t("refundCurrency")}>
          {[false, true].map((value) => (
            <button
              key={String(value)}
              type="button"
              className={s.segment}
              aria-pressed={inForeign === value}
              onClick={() => setInForeign(value)}
            >
              <bdi dir="ltr">{value ? foreign.currency : txn.amount.currency}</bdi>
            </button>
          ))}
        </div>
      ) : null}
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${formId}-amount`}>
          {t("refundAmount", { currency })}
        </label>
        <MoneyInput
          id={`${formId}-amount`}
          className={appStyles.input}
          value={amount}
          onValueChange={setAmount}
          aria-invalid={(parsed && !parsed.ok) || (submitted && !parsed) ? true : undefined}
        />
        {parsed && !parsed.ok ? (
          <span className={appStyles.error}>
            {amountT(`errors.${parsed.error}`, {
              example: formatMoney({ amount: "1250.5", currency }, locale, { display: "none" }),
              currency,
              count: exponentOf(currency),
            })}
          </span>
        ) : null}
      </div>
      {inForeign ? (
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${formId}-charged`}>
            {t("refundReceived", { currency: txn.amount.currency })}
          </label>
          <MoneyInput
            id={`${formId}-charged`}
            className={appStyles.input}
            value={charged}
            onValueChange={setCharged}
            aria-invalid={submitted && !parsedCharged?.ok ? true : undefined}
          />
          <span className={appStyles.hint}>{t("refundReceivedHint")}</span>
        </div>
      ) : null}
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${formId}-account`}>
          {t("refundAccount")}
        </label>
        <Select id={`${formId}-account`} value={accountId} onValueChange={setAccountId}>
          {choices.map((a) => (
            <option key={a.id} value={a.id}>
              {`${accountName(a)} · ${a.currency}`}
            </option>
          ))}
        </Select>
      </div>
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${formId}-date`}>
          {t("date")}
        </label>
        <input
          id={`${formId}-date`}
          type="date"
          className={appStyles.input}
          value={date}
          min={txn.effective_date}
          max={todayIn(user.time_zone)}
          onChange={(e) => setDate(e.target.value)}
        />
      </div>
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${formId}-why`}>
          {t("refundWhy")}
        </label>
        <input
          id={`${formId}-why`}
          className={appStyles.input}
          value={description}
          maxLength={200}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <p className={appStyles.hint}>{t("refundRule")}</p>
    </form>
  );
}

function RefundHistory({ txn }: { txn: TransactionDetail }) {
  const t = useTranslations("app.transactions.detail");
  const locale = useLocale() as Locale;
  const refunds = txn.refunds;
  if (!refunds || refunds.items.length === 0) return null;
  return (
    <div className={s.refundBox}>
      <p className={s.refundTotals}>
        {t("refundTotals", {
          refunded: formatMoney(refunds.refunded, locale),
          left: formatMoney(refunds.refundable, locale),
        })}
      </p>
      <ul className={s.refundList}>
        {refunds.items.map((r) => (
          <li key={r.id} className={cn(r.status === "reversed" && s.voided)}>
            <bdi>{formatDate(`${r.effective_date}T12:00:00Z`, locale, "UTC")}</bdi>
            <span dir="auto">{r.description ?? ""}</span>
            <bdi dir="ltr" className={appStyles.num}>
              {formatMoney(r.amount, locale)}
            </bdi>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Facts({ txn }: { txn: TransactionDetail }) {
  const t = useTranslations("app.transactions.detail");
  const types = useTranslations("app.transactions.types");
  const list = useTranslations("app.transactions");
  const fx = useTranslations("app.fx");
  const locale = useLocale() as Locale;
  const user = useUser();
  const categoryName = useCategoryName();
  const accountName = useAccountName();
  const transfer = txn.type === "transfer" && txn.counter_account;

  return (
    <div className={s.detail}>
      <div className={s.detailTop}>
        <TxnAmount txn={txn} className={s.detailAmount} />
        <span className={s.detailTags}>
          <Tag>{types(txn.type)}</Tag>
          {txn.status === "reversed" ? <Tag>{list("voided")}</Tag> : null}
        </span>
      </div>
      {txn.status === "reversed" ? <Notice tone="info">{t("voidedNote")}</Notice> : null}
      {txn.trip_link ? <Notice tone="info">{t("tripNote")}</Notice> : null}
      {txn.type === "refund" ? <Notice tone="info">{t("refundNote")}</Notice> : null}
      <RefundHistory txn={txn} />
      <dl className={s.facts}>
        <div>
          <dt>{t("date")}</dt>
          <dd>
            <bdi>{formatDate(`${txn.effective_date}T12:00:00Z`, locale, "UTC")}</bdi>
          </dd>
        </div>
        {txn.category ? (
          <div>
            <dt>{t("category")}</dt>
            <dd className={s.factWithIcon}>
              <CategoryIcon category={txn.category} size="sm" />
              <span dir="auto">{categoryName(txn.category)}</span>
            </dd>
          </div>
        ) : null}
        {transfer && txn.counter_account ? (
          <>
            <div>
              <dt>{t("from")}</dt>
              <dd dir="auto">{txn.account ? accountName(txn.account) : ""}</dd>
            </div>
            <div>
              <dt>{t("to")}</dt>
              <dd dir="auto">{accountName(txn.counter_account)}</dd>
            </div>
          </>
        ) : (
          <div>
            <dt>{t("account")}</dt>
            <dd dir="auto">{txn.account ? accountName(txn.account) : t("noAccount")}</dd>
          </div>
        )}
        {txn.trip_link ? (
          <div>
            <dt>{t("trip")}</dt>
            <dd>
              <Link href={`/app/trips/${txn.trip_link.trip_id}`} className={appStyles.linkBtn}>
                <bdi>{txn.trip_link.trip_name}</bdi>
              </Link>
            </dd>
          </div>
        ) : null}
        {txn.original_amount ? (
          <div>
            <dt>{txn.trip_link ? t("tripAmount") : t("enteredAs")}</dt>
            <dd>
              <bdi dir="ltr" className={appStyles.num}>
                {formatMoney(txn.original_amount, locale)}
              </bdi>
            </dd>
          </div>
        ) : null}
        {txn.fx && txn.original_amount ? (
          <div>
            <dt>{t("conversion")}</dt>
            <dd>
              <bdi dir="ltr" className={appStyles.num}>
                {fx("rateLine", {
                  from: txn.original_amount.currency,
                  rate: txn.fx.rate,
                  to: txn.amount.currency,
                })}
              </bdi>
              <span className={s.factNote}>
                {txn.fx.method === "reference" && txn.fx.rate_date
                  ? txn.fx.upstream === "ECB"
                    ? fx("sourceEcb", {
                        date: formatDate(`${txn.fx.rate_date}T12:00:00Z`, locale, "UTC"),
                      })
                    : fx("sourceBlended", {
                        date: formatDate(`${txn.fx.rate_date}T12:00:00Z`, locale, "UTC"),
                      })
                  : t(`fxMethod.${txn.fx.method}`)}
              </span>
            </dd>
          </div>
        ) : null}
        {txn.debt ? (
          <div>
            <dt>{t("debt")}</dt>
            <dd dir="auto">{txn.debt.counterparty}</dd>
          </div>
        ) : null}
        {txn.debt_amount && txn.debt_amount.currency !== txn.amount.currency ? (
          <div>
            <dt>{t("debtAmount")}</dt>
            <dd>
              <bdi dir="ltr" className={appStyles.num}>
                {formatMoney(txn.debt_amount, locale)}
              </bdi>
            </dd>
          </div>
        ) : null}
        {txn.description ? (
          <div>
            <dt>{t("description")}</dt>
            <dd dir="auto">{txn.description}</dd>
          </div>
        ) : null}
        {txn.notes ? (
          <div>
            <dt>{t("note")}</dt>
            <dd dir="auto" className={s.noteText}>
              {txn.notes}
            </dd>
          </div>
        ) : null}
      </dl>
      <div>
        <h3 className={s.subTitle}>{t("history")}</h3>
        <ol className={s.timeline}>
          {txn.changes.map((change) => (
            <li key={`${change.action}-${change.at}`}>
              {t.rich(`changes.${change.action}`, {
                at: formatDateTime(change.at, locale, user.time_zone),
                bdi,
              })}
            </li>
          ))}
        </ol>
      </div>
      {txn.debt ? (
        <p className={cn(appStyles.muted, appStyles.small)}>
          {t("debtKind")}{" "}
          <Link href="/app/recurring" className={appStyles.linkBtn}>
            {t("openDebts")}
          </Link>
        </p>
      ) : txn.type !== "income" && txn.type !== "expense" && txn.account ? (
        <p className={cn(appStyles.muted, appStyles.small)}>
          {t("otherKind")}{" "}
          <Link href={`/app/accounts/${txn.account.id}`} className={appStyles.linkBtn}>
            {t("openAccount")}
          </Link>
        </p>
      ) : null}
    </div>
  );
}

function CorrectForm({
  formId,
  txn,
  busy,
  setBusy,
  onDone,
  onError,
}: {
  formId: string;
  txn: TransactionDetail;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onDone: (updated: TransactionDetail) => void;
  onError: (text: string) => void;
}) {
  const t = useTranslations("app.transactions.detail");
  const quick = useTranslations("app.quickAdd");
  const errors = useTranslations("app.transactions.errors");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, categories, currencies } = useFinance();
  const message = useFinanceError();
  const accountName = useAccountName();
  const id = useId();

  // Converted entries are corrected in the currency they were entered in; the stored rate is
  // kept (never re-rated), except when the charged amount was typed by hand.
  const entered = txn.original_amount ?? txn.amount;
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const exponent = exponentOf(entered.currency);
  const current = txn.account ?? { id: "", name: null, system_key: null };
  const typedCharge = txn.fx?.method === "actual_amount";
  // Same currency only: a correction never converts money silently.
  const accountOptions: { id: string; name: string | null; system_key: string | null }[] =
    accounts.filter((a) => a.currency === txn.amount.currency);
  if (!accountOptions.some((a) => a.id === current.id)) {
    // The original account is archived: the entry can stay on it, but nothing new goes there.
    accountOptions.unshift(current);
  }
  const kind = txn.type as "income" | "expense";
  const categoryOptions = categories.filter(
    (c) => c.kind === kind && (c.status === "active" || c.id === txn.category?.id),
  );
  if (txn.category && !categoryOptions.some((c) => c.id === txn.category?.id)) {
    categoryOptions.push(txn.category);
  }

  const [amount, setAmount] = useState(() => editableAmount(entered.amount, locale));
  const [charged, setCharged] = useState(() => editableAmount(txn.amount.amount, locale));
  const [accountId, setAccountId] = useState(current.id);
  const [categoryId, setCategoryId] = useState<string | null>(txn.category?.id ?? null);
  const [date, setDate] = useState(txn.effective_date);
  const [description, setDescription] = useState(txn.description ?? "");
  const [note, setNote] = useState(txn.notes ?? "");
  const [submitted, setSubmitted] = useState(false);
  const today = todayIn(user.time_zone);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    const parsed = parseAmount(amount, locale, exponent);
    const chargedParsed = typedCharge
      ? parseAmount(charged, locale, exponentOf(txn.amount.currency))
      : null;
    if (!parsed.ok || !categoryId || !date || date > today) return;
    if (chargedParsed && !chargedParsed.ok) return;
    const changes: Parameters<typeof financeApi.correct>[2] = {};
    if (parsed.amount !== entered.amount) {
      changes.amount = { amount: parsed.amount, currency: entered.currency };
    }
    if (chargedParsed?.ok && (chargedParsed.amount !== txn.amount.amount || changes.amount)) {
      changes.conversion = { method: "actual_amount", account_amount: chargedParsed.amount };
      changes.amount ??= { amount: parsed.amount, currency: entered.currency };
    }
    if (accountId !== current.id) changes.account_id = accountId;
    if (categoryId !== txn.category?.id) changes.category_id = categoryId;
    if (date !== txn.effective_date) changes.effective_date = date;
    if (description.trim() !== (txn.description ?? "")) {
      changes.description = description.trim() || null;
    }
    if (note.trim() !== (txn.notes ?? "")) changes.notes = note.trim() || null;
    if (Object.keys(changes).length === 0) {
      onError(errors("no_change"));
      return;
    }
    setBusy(true);
    try {
      onDone(await financeApi.correct(txn.id, txn.version, changes));
    } catch (error) {
      if (!handleAuthError(error)) onError(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form id={formId} className={appStyles.form} onSubmit={submit} noValidate>
      <AmountField
        label={txn.original_amount ? t("enteredAs") : quick("amount")}
        currency={entered.currency}
        exponent={exponent}
        value={amount}
        onChange={setAmount}
        showErrors={submitted}
      />
      {typedCharge ? (
        <AmountField
          label={t("charged")}
          hint={t("chargedHint")}
          currency={txn.amount.currency}
          exponent={exponentOf(txn.amount.currency)}
          value={charged}
          onChange={setCharged}
          showErrors={submitted}
        />
      ) : txn.original_amount ? (
        <p className={appStyles.hint}>{t("keepsRate")}</p>
      ) : null}
      <div className={appStyles.field}>
        <span className={appStyles.label}>{quick("category")}</span>
        <CategoryPicker
          name={`${id}-category`}
          legend={quick("category")}
          categories={categoryOptions}
          value={categoryId}
          onChange={setCategoryId}
          invalid={submitted && !categoryId}
          limit={8}
        />
      </div>
      <div className={s.fieldsRow}>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-account`}>
            {kind === "expense" ? quick("paidFrom") : quick("receivedIn")}
          </label>
          <Select
            id={`${id}-account`}
            value={accountId}
            onValueChange={setAccountId}
            disabled={busy}
          >
            {accountOptions.map((a) => (
              <option key={a.id} value={a.id}>
                {accountName(a)}
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
            max={today}
            onChange={(event) => setDate(event.target.value)}
            aria-invalid={submitted && (!date || date > today) ? true : undefined}
          />
        </div>
      </div>
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${id}-description`}>
          {t("description")}
        </label>
        <input
          id={`${id}-description`}
          className={appStyles.input}
          dir="auto"
          maxLength={200}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${id}-note`}>
          {t("note")}
        </label>
        <textarea
          id={`${id}-note`}
          className={cn(appStyles.input, s.note)}
          dir="auto"
          rows={2}
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>
    </form>
  );
}
