"use client";

import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useId, useMemo, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import {
  ACCOUNT_TYPES,
  type Account,
  type AccountType,
  accountsApi,
  type CurrencyInfo,
  newIdempotencyKey,
} from "@/lib/accounts";
import { localeTag } from "@/lib/format";
import { parseAmount } from "@/lib/money";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { Button, Notice } from "../ui";
import s from "./accounts.module.css";
import { AccountDialog, AmountField, TYPE_ICONS, todayIn, useErrorMessage } from "./parts";
import { Select } from "../select";

const NAME_MAX = 100;
const DISALLOWED = /[\p{Cc}\p{Cf}]/u;

function nameError(name: string): "required" | "tooLong" | "characters" | null {
  const trimmed = name.trim();
  if (!trimmed) return "required";
  if (trimmed.length > NAME_MAX) return "tooLong";
  if (DISALLOWED.test(trimmed)) return "characters";
  return null;
}

/** Add an account: type, name, currency, and optionally what it holds today. */
export function CreateAccountDialog({
  open,
  onOpenChange,
  currencies,
  suggestedCurrency,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currencies: CurrencyInfo[];
  /** The currency most of the person's accounts already use, if any. */
  suggestedCurrency?: string;
  onCreated: (account: Account) => void;
}) {
  const t = useTranslations("app.accounts.form");
  const types = useTranslations("app.accounts.types");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const message = useErrorMessage();
  const id = useId();

  // Their preferred currency, else the one their accounts mostly use, else US dollars.
  const defaultCurrency =
    [user.preferred_currency, suggestedCurrency, "USD"].find(
      (code) => code && currencies.some((c) => c.code === code),
    ) ??
    currencies[0]?.code ??
    "";
  const [type, setType] = useState<AccountType>("cash");
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [opening, setOpening] = useState("");
  const [openedOn, setOpenedOn] = useState(() => todayIn(user.time_zone));
  const [description, setDescription] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // One key for this dialog: a retry after a lost response can't create a second account.
  const [key, setKey] = useState(newIdempotencyKey);

  const names = useMemo(() => {
    const display = new Intl.DisplayNames([localeTag(locale)], { type: "currency" });
    return currencies
      .map((c) => ({ ...c, label: `${c.code} · ${display.of(c.code) ?? c.code}` }))
      .sort((a, b) => a.code.localeCompare(b.code));
  }, [currencies, locale]);
  const exponent = currencies.find((c) => c.code === currency)?.exponent ?? 2;
  const today = todayIn(user.time_zone);

  const reset = () => {
    setType("cash");
    setName("");
    setCurrency(defaultCurrency);
    setOpening("");
    setOpenedOn(todayIn(user.time_zone));
    setDescription("");
    setSubmitted(false);
    setFailure(null);
    setKey(newIdempotencyKey());
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    const parsedOpening = opening.trim()
      ? parseAmount(opening, locale, exponent, { allowNegative: true })
      : null;
    if (nameError(name) || (parsedOpening && !parsedOpening.ok) || !currency) return;
    setBusy(true);
    try {
      const account = await accountsApi.create(
        {
          name: name.trim(),
          type,
          currency,
          description: description.trim() || null,
          opened_on: openedOn,
          ...(parsedOpening?.ok
            ? { opening_balance: { amount: parsedOpening.amount, currency } }
            : {}),
        },
        key,
      );
      onCreated(account);
      onOpenChange(false);
      reset();
    } catch (error) {
      if (handleAuthError(error)) return;
      setFailure(message(error));
      // The server refused this request for good: a corrected one needs a fresh key.
      if (error instanceof ApiError && error.kind !== "unavailable") setKey(newIdempotencyKey());
    } finally {
      setBusy(false);
    }
  };

  const nameProblem = submitted ? nameError(name) : null;

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
      title={t("createTitle")}
      lead={t("createLead")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {busy ? t("creating") : t("create")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        <fieldset className={s.types}>
          <legend className={`${appStyles.label} ${appStyles.srOnly}`}>{t("type")}</legend>
          {ACCOUNT_TYPES.map((value) => {
            const Icon = TYPE_ICONS[value];
            return (
              <label key={value} className={s.typeOption}>
                <input
                  type="radio"
                  name={`${id}-type`}
                  value={value}
                  checked={type === value}
                  onChange={() => setType(value)}
                />
                <Icon aria-hidden="true" />
                <span>{types(value)}</span>
              </label>
            );
          })}
        </fieldset>

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-name`}>
            {t("name")}
          </label>
          <input
            id={`${id}-name`}
            className={appStyles.input}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={NAME_MAX + 20}
            dir="auto"
            autoComplete="off"
            aria-invalid={nameProblem ? true : undefined}
            aria-describedby={nameProblem ? `${id}-name-error` : undefined}
          />
          {nameProblem ? (
            <span id={`${id}-name-error`} className={appStyles.error}>
              {t(`nameErrors.${nameProblem}`)}
            </span>
          ) : null}
        </div>

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-currency`}>
            {t("currency")}
          </label>
          <Select
            id={`${id}-currency`}
            value={currency}
            onValueChange={setCurrency}
            aria-describedby={`${id}-currency-hint`}
          >
            {names.map((c) => (
              <option key={c.code} value={c.code}>
                {c.label}
              </option>
            ))}
          </Select>
          <span id={`${id}-currency-hint`} className={appStyles.hint}>
            {t("currencyHint")}
          </span>
        </div>

        <AmountField
          label={t("opening")}
          hint={t("openingHint")}
          currency={currency}
          exponent={exponent}
          value={opening}
          onChange={setOpening}
          allowNegative
          showErrors={submitted}
          required={false}
        />

        <div className={appStyles.fieldRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-opened`}>
              {t("openedOn")}
            </label>
            <input
              id={`${id}-opened`}
              type="date"
              className={appStyles.input}
              value={openedOn}
              max={today}
              onChange={(event) => setOpenedOn(event.target.value || today)}
            />
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-description`}>
              {t("description")}
            </label>
            <input
              id={`${id}-description`}
              className={appStyles.input}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={500}
              dir="auto"
              placeholder={t("optional")}
            />
          </div>
        </div>

        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </form>
    </AccountDialog>
  );
}

/** Rename an account, or change its description. Type and currency never change. */
export function EditAccountDialog({
  account,
  open,
  onOpenChange,
  onSaved,
}: {
  account: Account;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (account: Account) => void;
}) {
  const t = useTranslations("app.accounts.form");
  const { handleAuthError } = useAuth();
  const message = useErrorMessage();
  const id = useId();
  const [name, setName] = useState(account.name ?? "");
  const [description, setDescription] = useState(account.description ?? "");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const nameProblem = submitted ? nameError(name) : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (nameError(name)) return;
    const changes: { name?: string; description?: string | null } = {};
    if (name.trim() !== (account.name ?? "")) changes.name = name.trim();
    if ((description.trim() || null) !== account.description)
      changes.description = description.trim() || null;
    if (Object.keys(changes).length === 0) {
      onOpenChange(false);
      return;
    }
    setBusy(true);
    try {
      onSaved(await accountsApi.update(account.id, account.version, changes));
      onOpenChange(false);
    } catch (error) {
      if (handleAuthError(error)) return;
      setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (next) {
          setName(account.name ?? "");
          setDescription(account.description ?? "");
          setSubmitted(false);
          setFailure(null);
        }
      }}
      title={t("editTitle")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {busy ? t("saving") : t("save")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-name`}>
            {t("name")}
          </label>
          <input
            id={`${id}-name`}
            className={appStyles.input}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={NAME_MAX + 20}
            dir="auto"
            aria-invalid={nameProblem ? true : undefined}
            aria-describedby={nameProblem ? `${id}-name-error` : undefined}
          />
          {nameProblem ? (
            <span id={`${id}-name-error`} className={appStyles.error}>
              {t(`nameErrors.${nameProblem}`)}
            </span>
          ) : null}
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-description`}>
            {t("description")}
          </label>
          <input
            id={`${id}-description`}
            className={appStyles.input}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={500}
            dir="auto"
            placeholder={t("optional")}
          />
        </div>
        <p className={appStyles.hint}>{t("fixedFields")}</p>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </form>
    </AccountDialog>
  );
}
