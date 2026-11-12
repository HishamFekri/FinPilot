"use client";

import { CircleCheck } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { type Category, financeApi, type Kind } from "@/lib/finance";
import { formatMoney, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import { todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { Button, buttonClass, Notice } from "../ui";
import { type QuickPreset, useFinance } from "./finance-provider";
import { templatesApi } from "@/lib/everyday";
import { editableAmount } from "@/lib/money";
import s from "./finance.module.css";
import { ConversionPanel, useConversion } from "./conversion";
import { CategoryPicker, useAccountName, useCategoryName, useFinanceError } from "./parts";
import { MoneyInput, useMoneyHint } from "../money-input";
import { SearchSelect } from "../search-select";

/**
 * The account to use for an amount in `code`, when the person hasn't chosen one: the default
 * account if it's in that currency, else their first active account in it. Null when none is, and
 * the amount then goes through the explicit conversion step instead. Never a silent conversion.
 */
export function accountForCurrency(
  accounts: { id: string; currency: string; status: string }[],
  code: string,
  defaultAccountId: string | null,
): string | null {
  const active = accounts.filter((a) => a.status === "active" && a.currency === code);
  return (active.find((a) => a.id === defaultAccountId) ?? active[0])?.id ?? null;
}

/** Favourites first, then the most recently used, then the usual order. */
export function orderCategories(categories: Category[]): Category[] {
  return categories
    .map((c, i) => ({ c, i }))
    .sort(
      (a, b) =>
        Number(Boolean(b.c.is_favourite)) - Number(Boolean(a.c.is_favourite)) ||
        (b.c.last_used_at ?? "").localeCompare(a.c.last_used_at ?? "") ||
        a.i - b.i,
    )
    .map(({ c }) => c);
}

/** The category used most recently for this kind of entry, if any is still active. */
export function lastUsed(categories: Category[], kind: Kind): string | null {
  let best: Category | null = null;
  for (const c of categories) {
    if (c.kind !== kind || c.status !== "active" || !c.last_used_at) continue;
    if (!best || (best.last_used_at ?? "") < c.last_used_at) best = c;
  }
  return best?.id ?? null;
}

type Mode = "inline" | "dialog";

/**
 * Quick Add: the main thing FinPilot is for. Amount first, then a category, then save. Everything
 * else has a sensible default: an expense, the default account, today in the person's time zone,
 * and the category they used last for this kind of entry (or none, so they choose).
 *
 * Duplicate protection: each distinct draft gets one idempotency key. Retrying the same draft
 * (a double press, a network retry) reuses it, so the server records it once; any change to the
 * draft gets a new key.
 */
export function QuickAdd({
  mode,
  onClose,
  amountId,
  preset,
}: {
  mode: Mode;
  onClose?: () => void;
  /** A stable id for the amount input, so a shortcut elsewhere can focus it. */
  amountId?: string;
  /** Details to start with (a template, a calendar day). Mount with a new key to apply. */
  preset?: QuickPreset | null;
}) {
  const t = useTranslations("app.quickAdd");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, categories, currencies, defaultAccountId, changed } = useFinance();
  const message = useFinanceError();
  const categoryName = useCategoryName();
  const accountName = useAccountName();
  const id = useId();
  const amountRef = useRef<HTMLInputElement>(null);
  const moneyHint = useMoneyHint();

  const startAccount = accounts.find((a) => a.id === defaultAccountId)?.id ?? accounts[0]?.id ?? "";
  const presetAccount =
    preset?.accountId && accounts.some((a) => a.id === preset.accountId && a.status === "active")
      ? preset.accountId
      : null;
  const [kind, setKind] = useState<Kind>(preset?.kind ?? "expense");
  const [amount, setAmount] = useState(() =>
    preset?.amount ? editableAmount(preset.amount, locale) : "",
  );
  const [chosen, setChosen] = useState<Record<Kind, string | null>>(() => ({
    expense: lastUsed(categories, "expense"),
    income: lastUsed(categories, "income"),
    ...(preset?.categoryId ? { [preset.kind ?? "expense"]: preset.categoryId } : {}),
  }));
  // An amount preset in another currency (a template) starts on an account in that currency when
  // there is one, so nothing needs converting.
  const presetMatch =
    !presetAccount && preset?.currency
      ? accountForCurrency(accounts, preset.currency, defaultAccountId)
      : null;
  const [accountId, setAccountId] = useState(presetAccount ?? presetMatch ?? startAccount);
  // Whether the person chose the account, or the currency, themselves: a choice is never changed
  // for them. An account they picked stays even when they pick a currency it doesn't use (the
  // amount is then converted, explicitly); a currency they picked stays when they change account.
  const [accountChosen, setAccountChosen] = useState(Boolean(presetAccount));
  const [currencyChosen, setCurrencyChosen] = useState(Boolean(preset?.currency));
  // The currency the amount is in. Null means the account's own currency.
  const [entered, setEntered] = useState<string | null>(() => {
    const own = accounts.find(
      (a) => a.id === (presetAccount ?? presetMatch ?? startAccount),
    )?.currency;
    return preset?.currency && preset.currency !== own ? preset.currency : null;
  });
  // A template always uses today; a calendar day brings its date.
  const [date, setDate] = useState(() => preset?.date ?? todayIn(user.time_zone));
  const [description, setDescription] = useState(preset?.description ?? "");
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState<{
    category: string;
    amount: string;
    original: string | null;
    n: number;
  } | null>(null);
  const [farRate, setFarRate] = useState(false);
  const draftKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const savedCount = useRef(0);

  const today = todayIn(user.time_zone);
  const yesterday = new Date(Date.parse(`${today}T12:00:00Z`) - 864e5).toISOString().slice(0, 10);
  const options = orderCategories(
    categories.filter((c) => c.kind === kind && c.status === "active"),
  );
  const categoryId =
    chosen[kind] && options.some((c) => c.id === chosen[kind]) ? chosen[kind] : null;
  // If the chosen account was archived meanwhile, the default takes its place. The amount field
  // always shows the account's currency code, so a currency never changes silently.
  const account =
    accounts.find((a) => a.id === accountId) ??
    accounts.find((a) => a.id === startAccount) ??
    accounts[0];
  const accountCurrency = account?.currency ?? user.preferred_currency ?? "USD";
  const currency = entered ?? accountCurrency;
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const exponent = exponentOf(currency);
  const parsed = amount.trim() ? parseAmount(amount, locale, exponent) : null;
  const conversion = useConversion({
    active: currency !== accountCurrency,
    from: currency,
    to: accountCurrency,
    amount: parsed?.ok ? parsed.amount : null,
    date,
    toExponent: exponentOf(accountCurrency),
    fromExponent: exponent,
  });
  // The account's currency first, then the person's other accounts', then the usual ones.
  const currencyOptions = [
    ...new Set([
      accountCurrency,
      ...accounts.map((a) => a.currency),
      "USD",
      "EUR",
      "GBP",
      "TRY",
      ...currencies.map((c) => c.code).sort(),
    ]),
  ];
  const amountError =
    parsed && !parsed.ok ? parsed.error : !parsed && submitted ? ("empty" as const) : null;
  const categoryMissing = submitted && !categoryId;
  const dateInvalid = !date || date > today;
  const example = formatMoney({ amount: exponent ? "1250.5" : "1250", currency }, locale, {
    display: "none",
  });

  // The acknowledgement fades on its own; the next save replaces it.
  useEffect(() => {
    if (!saved) return;
    const timer = window.setTimeout(() => setSaved(null), 5000);
    return () => window.clearTimeout(timer);
  }, [saved]);

  if (accounts.length === 0 || !account) {
    return (
      <div className={s.emptyState}>
        <p className={s.emptyTitle}>{t("noAccountsTitle")}</p>
        <p className={appStyles.muted}>{t("noAccountsBody")}</p>
        <Link href="/app/accounts" className={buttonClass("secondary")} onClick={onClose}>
          {t("noAccountsCta")}
        </Link>
      </div>
    );
  }

  const dirty = Boolean(amount.trim() || description.trim() || note.trim());

  const clearDraft = () => {
    setAmount("");
    setDescription("");
    setNote("");
    setNoteOpen(false);
    setSubmitted(false);
    setFailure(null);
    conversion.reset();
    draftKey.current = null;
  };

  const save = async (another: boolean) => {
    if (busy) return;
    setSubmitted(true);
    setFailure(null);
    if (!parsed?.ok || !categoryId || dateInvalid || (conversion.active && !conversion.choice)) {
      if (!parsed?.ok) amountRef.current?.focus();
      return;
    }
    const body = {
      type: kind,
      account_id: account.id,
      amount: { amount: parsed.amount, currency },
      category_id: categoryId,
      effective_date: date,
      description: description.trim() || null,
      notes: note.trim() || null,
      ...(conversion.active && conversion.choice ? { conversion: conversion.choice } : {}),
      ...(preset?.templateId ? { source: "template" as const } : {}),
    };
    const fingerprint = JSON.stringify(body);
    if (draftKey.current?.fingerprint !== fingerprint) {
      draftKey.current = { fingerprint, key: newIdempotencyKey() };
    }
    setBusy(true);
    try {
      const result = await financeApi.record(body, draftKey.current.key);
      if (preset?.templateId) void templatesApi.used(preset.templateId).catch(() => undefined);
      setFarRate(Boolean(result.warnings?.includes("rate_far_from_reference")));
      const category = categories.find((c) => c.id === categoryId);
      setSaved({
        category: category ? categoryName(category) : "",
        amount: formatMoney(
          {
            ...result.amount,
            amount: kind === "expense" ? `-${result.amount.amount}` : result.amount.amount,
          },
          locale,
          { signed: true },
        ),
        original: result.original_amount ? formatMoney(result.original_amount, locale) : null,
        n: (savedCount.current += 1),
      });
      clearDraft();
      await changed();
      if (mode === "dialog" && !another) {
        onClose?.();
      } else {
        amountRef.current?.focus();
      }
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void save(false);
  };

  // Enter saves from anywhere in the form; Shift+Enter saves and keeps going. In the note, plain
  // Enter is a new line, and Ctrl/Cmd+Enter saves.
  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    const target = event.target as HTMLElement;
    if (target.tagName === "BUTTON" || target.tagName === "A") return;
    if (target.tagName === "TEXTAREA" && !(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    void save(event.shiftKey);
  };

  const chooseCurrency = (code: string) => {
    setCurrencyChosen(true);
    if (!accountChosen) {
      const match = accountForCurrency(accounts, code, defaultAccountId);
      if (match) {
        setAccountId(match);
        setEntered(null);
        return;
      }
    }
    setEntered(code === accountCurrency ? null : code);
  };

  const chooseAccount = (next: string) => {
    setAccountChosen(true);
    // A currency the person picked stays (converted explicitly below); otherwise the amount is in
    // the new account's currency.
    const nextCurrency = accounts.find((a) => a.id === next)?.currency;
    setAccountId(next);
    setEntered(currencyChosen && currency !== nextCurrency ? currency : null);
  };

  const switchKind = (next: Kind) => {
    setKind(next);
    setFailure(null);
  };

  return (
    <form
      className={cn(s.composer, mode === "dialog" && s.composerBare)}
      onSubmit={onSubmit}
      onKeyDown={onKeyDown}
      noValidate
      aria-labelledby={mode === "inline" ? `${id}-title` : undefined}
    >
      <div className={s.composerTop}>
        {mode === "inline" ? (
          <h2 id={`${id}-title`} className={s.composerTitle}>
            {t("title")}
          </h2>
        ) : (
          <span />
        )}
        <div className={s.segmented} role="group" aria-label={t("typeLabel")}>
          {(["expense", "income"] as const).map((k) => (
            <button
              key={k}
              type="button"
              className={s.segment}
              data-kind={k}
              aria-pressed={kind === k}
              onClick={() => switchKind(k)}
            >
              {t(k)}
            </button>
          ))}
        </div>
      </div>

      <div>
        <label htmlFor={amountId ?? `${id}-amount`} className={appStyles.srOnly}>
          {t("amount")}
        </label>
        <div className={s.amountRow} data-invalid={amountError ? true : undefined}>
          <SearchSelect
            size="sm"
            className={s.currencySelect}
            aria-label={t("currency")}
            searchLabel={t("searchCurrency")}
            value={currency}
            dir="ltr"
            onValueChange={chooseCurrency}
          >
            {currencyOptions.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </SearchSelect>
          <MoneyInput
            ref={amountRef}
            id={amountId ?? `${id}-amount`}
            className={s.amountInput}
            placeholder={formatMoney({ amount: exponent ? "0.00" : "0", currency }, locale, {
              display: "none",
            })}
            value={amount}
            onValueChange={setAmount}
            onRejected={moneyHint.onRejected}
            aria-invalid={amountError ? true : undefined}
            aria-describedby={`${id}-echo`}
            data-autofocus={mode === "dialog" ? "" : undefined}
          />
        </div>
        <p id={`${id}-echo`} className={cn(s.echo, amountError && s.echoError)} aria-live="polite">
          {moneyHint.hint
            ? moneyHint.hint
            : amountError
              ? amountT(`errors.${amountError}`, { example, currency, count: exponent })
              : parsed?.ok
                ? amountT.rich("reads", {
                    amount: formatMoney({ amount: parsed.amount, currency }, locale),
                    bdi: (chunks) => <bdi dir="ltr">{chunks}</bdi>,
                  })
                : ""}
        </p>
      </div>

      <ConversionPanel
        conversion={conversion}
        accountLabel={accountName(account)}
        kind={kind}
        showErrors={submitted}
      />

      {options.length === 0 ? (
        <Notice tone="info">{t("noCategories", { kind })}</Notice>
      ) : (
        <div>
          <CategoryPicker
            key={kind}
            name={`${id}-category`}
            legend={t("category")}
            categories={options}
            value={categoryId}
            onChange={(value) => setChosen((prev) => ({ ...prev, [kind]: value }))}
            invalid={categoryMissing}
            describedBy={categoryMissing ? `${id}-category-error` : undefined}
            limit={mode === "dialog" ? 8 : 5}
          />
          {options.length > 6 ? (
            <div className={s.findCategory}>
              <SearchSelect
                size="sm"
                aria-label={t("findCategory")}
                searchLabel={t("findCategory")}
                placeholder={t("findCategory")}
                value={categoryId}
                onValueChange={(value) => setChosen((prev) => ({ ...prev, [kind]: value }))}
                options={options.map((c) => ({ value: c.id, label: categoryName(c) }))}
              />
            </div>
          ) : null}
          {categoryMissing ? (
            <p id={`${id}-category-error`} className={cn(s.echo, s.echoError)} role="alert">
              {t("chooseCategory")}
            </p>
          ) : null}
        </div>
      )}

      <div className={s.fieldsRow}>
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-account`}>
            {kind === "expense" ? t("paidFrom") : t("receivedIn")}
          </label>
          <SearchSelect
            id={`${id}-account`}
            value={account.id}
            onValueChange={chooseAccount}
            searchLabel={t("searchAccount")}
          >
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {`${accountName(a)} · ${a.currency}`}
              </option>
            ))}
          </SearchSelect>
        </div>
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-date`}>
            {t("date")}
          </label>
          <div className={s.dateRow}>
            <input
              id={`${id}-date`}
              type="date"
              className={appStyles.input}
              value={date}
              max={today}
              onChange={(event) => setDate(event.target.value)}
              aria-invalid={submitted && dateInvalid ? true : undefined}
            />
            <div className={s.dateChips} role="group" aria-label={t("quickDates")}>
              <button
                type="button"
                className={s.dateChip}
                aria-pressed={date === today}
                onClick={() => setDate(today)}
              >
                {t("today")}
              </button>
              <button
                type="button"
                className={s.dateChip}
                aria-pressed={date === yesterday}
                onClick={() => setDate(yesterday)}
              >
                {t("yesterday")}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className={s.fieldsRow}>
        <div className={cn(appStyles.field, s.fullRow)}>
          <label className={s.compactLabel} htmlFor={`${id}-description`}>
            {t("description")}
          </label>
          <input
            id={`${id}-description`}
            className={appStyles.input}
            dir="auto"
            maxLength={200}
            placeholder={t("descriptionPlaceholder")}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
      </div>

      {noteOpen ? (
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-note`}>
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
            // It appears because the person just asked for it, so focus goes there.
            autoFocus
          />
        </div>
      ) : null}

      {failure ? <Notice tone="error">{failure}</Notice> : null}
      {farRate && saved ? <Notice tone="info">{t("farRate")}</Notice> : null}

      <div className={s.composerFoot}>
        <div className={s.footStart}>
          {noteOpen ? null : (
            <button type="button" className={appStyles.linkBtn} onClick={() => setNoteOpen(true)}>
              {t("addNote")}
            </button>
          )}
          <div aria-live="polite" className={s.savedSlot}>
            {saved ? (
              <p key={saved.n} className={s.saved}>
                <CircleCheck aria-hidden="true" />
                <span>
                  {t.rich(saved.original ? "savedConverted" : "saved", {
                    category: saved.category,
                    amount: saved.amount,
                    original: saved.original ?? "",
                    bdi: (chunks) => <bdi dir="ltr">{chunks}</bdi>,
                  })}
                </span>
              </p>
            ) : null}
          </div>
        </div>
        <div className={s.composerActions}>
          {mode === "dialog" ? (
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              {t("cancel")}
            </Button>
          ) : dirty ? (
            <Button variant="ghost" onClick={clearDraft} disabled={busy}>
              {t("clear")}
            </Button>
          ) : null}
          {mode === "dialog" ? (
            <Button onClick={() => void save(true)} disabled={busy}>
              {t("saveAnother")}
            </Button>
          ) : null}
          <Button type="submit" variant="primary" busy={busy}>
            {t("save")}
          </Button>
        </div>
      </div>
      <p className={s.shortcutHint}>
        {t.rich("shortcuts", { kbd: (chunks) => <kbd className={s.key}>{chunks}</kbd> })}
      </p>
    </form>
  );
}
