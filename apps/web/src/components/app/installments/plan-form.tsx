"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { installmentsApi, type PlanBody, type PlanPreview } from "@/lib/installments";
import { formatMoney, parseAmount } from "@/lib/money";
import { AccountDialog, AmountField, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import { useAccountName, useCategoryName } from "../finance/parts";
import { Select } from "../select";
import { Button, Notice } from "../ui";
import { useInstallmentError } from "./errors";
import s from "./installments.module.css";

type Origin = PlanBody["origin"];

function addMonth(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number) as [number, number, number];
  const next = new Date(Date.UTC(y, m, Math.min(d, 28)));
  return next.toISOString().slice(0, 10);
}

/**
 * A new installment plan. Three ways in:
 * - purchase: the full price counts as spending once; the account pays the down payment;
 * - loan: money arrived in an account (never income);
 * - existing: already running before FinPilot; only what remains is recorded (no money moves).
 * The schedule is previewed by the API before anything is saved.
 */
export function PlanDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (id: string) => void;
}) {
  const t = useTranslations("app.installments.form");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const message = useInstallmentError();
  const { accounts, categories, currencies, changed } = useFinance();
  const accountName = useAccountName();
  const categoryName = useCategoryName();
  const id = useId();
  const today = todayIn(user.time_zone);
  const active = accounts.filter((a) => a.status === "active");

  const [origin, setOrigin] = useState<Origin>("purchase");
  const [name, setName] = useState("");
  const [lender, setLender] = useState("");
  const [currency, setCurrency] = useState(user.preferred_currency ?? active[0]?.currency ?? "EUR");
  const [price, setPrice] = useState("");
  const [down, setDown] = useState("");
  const [principal, setPrincipal] = useState("");
  const [count, setCount] = useState("12");
  const [paidBefore, setPaidBefore] = useState("0");
  const [firstDue, setFirstDue] = useState(addMonth(today));
  const [startDate, setStartDate] = useState(today);
  const [interest, setInterest] = useState("");
  const [fee, setFee] = useState("");
  const [accountId, setAccountId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [key, setKey] = useState(() => crypto.randomUUID());

  const exponent = currencies.find((c) => c.code === currency)?.exponent ?? 2;
  const inCurrency = active.filter((a) => a.currency === currency);
  const expenseCategories = categories.filter((c) => c.kind === "expense" && c.status === "active");
  const amount = (text: string) => {
    const parsed = text.trim() ? parseAmount(text, locale, exponent) : null;
    return parsed?.ok ? parsed.amount : null;
  };

  // The account must be in the plan's currency: choose one when the currency changes.
  useEffect(() => {
    if (!inCurrency.some((a) => a.id === accountId)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAccountId(inCurrency[0]?.id ?? "");
    }
  }, [currency, inCurrency, accountId]);

  const body = (): PlanBody => ({
    origin,
    name: name.trim(),
    lender: lender.trim() || null,
    currency,
    installment_count: Number(count) || 0,
    first_due_date: firstDue,
    item_price: origin === "purchase" ? amount(price) : null,
    down_payment: origin === "purchase" ? (amount(down) ?? "0") : null,
    category_id: origin === "purchase" ? categoryId || null : null,
    account_id: origin === "existing" ? null : accountId || null,
    start_date: origin === "existing" ? null : startDate,
    principal: origin === "purchase" ? null : amount(principal),
    paid_before_count: origin === "existing" ? Number(paidBefore) || 0 : 0,
    interest_per_installment: amount(interest),
    fee_per_installment: amount(fee),
  });

  // Ask the API for the schedule whenever the inputs that shape it change.
  const shape = JSON.stringify([origin, currency, price, down, principal, count, paidBefore,
    firstDue, interest, fee]); // prettier-ignore
  useEffect(() => {
    if (!open) return;
    const draft = body();
    const ready =
      draft.installment_count > 0 &&
      (origin === "purchase" ? Boolean(draft.item_price) : Boolean(draft.principal));
    if (!ready) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPreview(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      installmentsApi
        .preview({ ...draft, name: draft.name || "-" })
        .then((p) => !cancelled && setPreview(p))
        .catch(() => !cancelled && setPreview(null));
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, open]);

  const submit = async () => {
    setSubmitted(true);
    setFailure(null);
    const draft = body();
    if (!draft.name || draft.installment_count < 1) return;
    if (origin === "purchase" && (!draft.item_price || !draft.category_id || !draft.account_id))
      return;
    if (origin === "loan" && (!draft.principal || !draft.account_id)) return;
    if (origin === "existing" && !draft.principal) return;
    setBusy(true);
    try {
      const plan = await installmentsApi.create(draft, key);
      await changed();
      onCreated(plan.id);
      onOpenChange(false);
      setKey(crypto.randomUUID());
    } catch (error) {
      if (!handleAuthError(error)) {
        setFailure(message(error));
        if (error instanceof ApiError && error.kind !== "unavailable") setKey(crypto.randomUUID());
      }
    } finally {
      setBusy(false);
    }
  };

  const missing = (value: unknown) => submitted && !value;

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
        <fieldset className={s.origins}>
          <legend className={appStyles.label}>{t("origin")}</legend>
          {(["purchase", "loan", "existing"] as const).map((o) => (
            <label key={o} className={s.origin} data-checked={origin === o || undefined}>
              <input
                type="radio"
                name={`${id}-origin`}
                value={o}
                checked={origin === o}
                onChange={() => setOrigin(o)}
              />
              <span className={s.originText}>
                <strong>{t(`origins.${o}`)}</strong>
                <span>{t(`originHelp.${o}`)}</span>
              </span>
            </label>
          ))}
        </fieldset>

        <div className={appStyles.fieldRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-name`}>
              {t("name")}
            </label>
            <input
              id={`${id}-name`}
              className={appStyles.input}
              value={name}
              maxLength={80}
              dir="auto"
              placeholder={t("namePlaceholder")}
              onChange={(e) => setName(e.target.value)}
              aria-invalid={missing(name.trim()) || undefined}
            />
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-lender`}>
              {t("lender")}
            </label>
            <input
              id={`${id}-lender`}
              className={appStyles.input}
              value={lender}
              maxLength={80}
              dir="auto"
              onChange={(e) => setLender(e.target.value)}
            />
          </div>
        </div>

        <div className={appStyles.fieldRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-currency`}>
              {t("currency")}
            </label>
            <Select id={`${id}-currency`} value={currency} onValueChange={setCurrency} dir="ltr">
              {[...new Set([currency, ...active.map((a) => a.currency), "TRY", "USD", "EUR"])].map(
                (c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ),
              )}
            </Select>
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-count`}>
              {origin === "existing" ? t("totalCount") : t("count")}
            </label>
            <input
              id={`${id}-count`}
              className={appStyles.input}
              inputMode="numeric"
              value={count}
              onChange={(e) => setCount(e.target.value.replace(/\D/g, "").slice(0, 3))}
            />
          </div>
        </div>

        {origin === "purchase" ? (
          <div className={appStyles.fieldRow}>
            <AmountField
              label={t("price")}
              currency={currency}
              exponent={exponent}
              value={price}
              onChange={setPrice}
              showErrors={submitted}
            />
            <AmountField
              label={t("down")}
              hint={t("downHint")}
              currency={currency}
              exponent={exponent}
              value={down}
              onChange={setDown}
              showErrors={submitted}
              required={false}
            />
          </div>
        ) : (
          <div className={appStyles.fieldRow}>
            <AmountField
              label={origin === "existing" ? t("remaining") : t("principal")}
              hint={origin === "existing" ? t("remainingHint") : t("principalHint")}
              currency={currency}
              exponent={exponent}
              value={principal}
              onChange={setPrincipal}
              showErrors={submitted}
            />
            {origin === "existing" ? (
              <div className={appStyles.field}>
                <label className={appStyles.label} htmlFor={`${id}-paid`}>
                  {t("paidBefore")}
                </label>
                <input
                  id={`${id}-paid`}
                  className={appStyles.input}
                  inputMode="numeric"
                  value={paidBefore}
                  onChange={(e) => setPaidBefore(e.target.value.replace(/\D/g, "").slice(0, 3))}
                />
              </div>
            ) : null}
          </div>
        )}

        {origin !== "existing" ? (
          <div className={appStyles.fieldRow}>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-account`}>
                {origin === "purchase" ? t("paidFrom") : t("receivedIn")}
              </label>
              {inCurrency.length ? (
                <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
                  {inCurrency.map((a) => (
                    <option key={a.id} value={a.id}>
                      {`${accountName(a)} · ${a.currency}`}
                    </option>
                  ))}
                </Select>
              ) : (
                <p className={appStyles.error}>{t("noAccount", { currency })}</p>
              )}
            </div>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-start`}>
                {origin === "purchase" ? t("purchaseDate") : t("loanDate")}
              </label>
              <input
                id={`${id}-start`}
                type="date"
                className={appStyles.input}
                value={startDate}
                max={today}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
          </div>
        ) : null}

        {origin === "purchase" ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-category`}>
              {t("category")}
            </label>
            <Select
              id={`${id}-category`}
              value={categoryId}
              onValueChange={setCategoryId}
              placeholder={t("chooseCategory")}
              aria-invalid={missing(categoryId)}
            >
              {expenseCategories.map((c) => (
                <option key={c.id} value={c.id}>
                  {categoryName(c)}
                </option>
              ))}
            </Select>
            <span className={appStyles.hint}>{t("categoryHint")}</span>
          </div>
        ) : null}

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-first`}>
            {origin === "existing" ? t("nextDue") : t("firstDue")}
          </label>
          <input
            id={`${id}-first`}
            type="date"
            className={appStyles.input}
            value={firstDue}
            onChange={(e) => setFirstDue(e.target.value)}
          />
        </div>

        <details className={s.more}>
          <summary>{t("interestTitle")}</summary>
          <p className={appStyles.hint}>{t("interestHelp")}</p>
          <div className={appStyles.fieldRow}>
            <AmountField
              label={t("interest")}
              currency={currency}
              exponent={exponent}
              value={interest}
              onChange={setInterest}
              showErrors={submitted}
              required={false}
            />
            <AmountField
              label={t("fee")}
              currency={currency}
              exponent={exponent}
              value={fee}
              onChange={setFee}
              showErrors={submitted}
              required={false}
            />
          </div>
        </details>

        {preview ? (
          <div className={s.preview} aria-live="polite">
            <p>
              {t("previewLine", {
                count: preview.count,
                amount: formatMoney(preview.per_installment[0]!, locale),
                date: formatDate(`${preview.due_dates[0]}T12:00:00Z`, locale, "UTC"),
                last: formatDate(`${preview.completion_date}T12:00:00Z`, locale, "UTC"),
              })}
            </p>
            <p className={s.note}>
              {t("previewTotal", {
                principal: formatMoney(preview.principal, locale),
                total: formatMoney(preview.total_to_pay, locale),
              })}
            </p>
            {origin === "purchase" ? <p className={s.note}>{t("previewSpending")}</p> : null}
            {origin === "loan" ? <p className={s.note}>{t("previewLoan")}</p> : null}
            {origin === "existing" ? <p className={s.note}>{t("previewExisting")}</p> : null}
          </div>
        ) : null}
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </div>
    </AccountDialog>
  );
}
