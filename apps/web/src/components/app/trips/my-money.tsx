"use client";

import { Lock, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { fxApi } from "@/lib/fx";
import {
  type DuplicateGroup,
  type Link,
  type Linkable,
  linksApi,
  type Position,
} from "@/lib/links";
import { editableAmount, formatMoney, isNegative, isZero, parseAmount } from "@/lib/money";
import type { Expense, Trip } from "@/lib/trips";
import { AccountDialog, Money, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import { useAccountName, useFinanceError } from "../finance/parts";
import { MoneyInput } from "../money-input";
import { Button, Notice } from "../ui";
import { isolate } from "./parts";
import s from "./trips.module.css";
import { Select } from "../select";

/**
 * The person's own money in a trip (FINANCIAL_RULES §11). Private: it comes only from the
 * owner-only endpoints, and nobody else in the trip can see any of it. Nothing here moves money
 * or posts anything without the person asking: "Record in my accounts" is always their choice.
 *
 * "Amount you paid" is cash; "Your share" is what the trip cost you, and is what counts as your
 * spending. Money friends pay back is never income.
 */

type Loaded = {
  position: Position;
  links: Link[];
  linkable: Linkable[];
  duplicates: DuplicateGroup[];
};

export function MyTripMoney({
  trip,
  revision,
  onChanged,
}: {
  trip: Trip;
  revision: number;
  onChanged: (text: string) => void;
}) {
  const t = useTranslations("app.trips.mine");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const [data, setData] = useState<{ for: string; value?: Loaded; failed?: boolean } | null>(null);
  const [record, setRecord] = useState<{ n: number; item: Linkable } | null>(null);
  const [review, setReview] = useState<{ n: number; link: Link } | null>(null);
  const [unlink, setUnlink] = useState<{ n: number; link: Link } | null>(null);
  const [dupesOpen, setDupesOpen] = useState(false);
  const [open, setOpen] = useState<"record" | "review" | "unlink" | null>(null);
  const [extra, setExtra] = useState(0);
  const stamp = `${revision}-${extra}`;

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      linksApi.position(trip.id),
      linksApi.list(trip.id),
      linksApi.linkable(trip.id),
      linksApi.duplicates({ trip_id: trip.id }),
    ])
      .then(([position, links, linkable, duplicates]) => {
        if (!cancelled) setData({ for: stamp, value: { position, links, linkable, duplicates } });
      })
      .catch((error: unknown) => {
        if (cancelled || handleAuthError(error)) return;
        setData({ for: stamp, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [trip.id, stamp, handleAuthError]);

  const done = (text: string) => {
    setOpen(null);
    setDupesOpen(false);
    setExtra((n) => n + 1);
    onChanged(text);
  };

  const value = data?.value;
  if (data?.failed) {
    return (
      <section className={s.card} aria-labelledby="mine-title">
        <div className={s.cardHead}>
          <h2 id="mine-title" className={s.cardTitle}>
            {t("title")}
          </h2>
        </div>
        <div className={s.cardBody}>
          <Notice tone="error">{t("loadFailed")}</Notice>
        </div>
      </section>
    );
  }
  if (!value) return null;
  const { position: p, links, linkable, duplicates } = value;
  const review_ = links.filter((l) => l.review.needed);
  const owed = !isZero(p.owed_to_me);
  const owes = !isZero(p.i_owe);
  const unrecorded = !isZero(p.unrecorded);
  const dupeCount = duplicates.reduce((n, g) => n + g.candidates.length, 0);
  const abs = (m: Position["unrecorded"]) =>
    formatMoney({ ...m, amount: m.amount.replace(/^-/, "") }, locale);

  return (
    <section className={s.card} aria-labelledby="mine-title">
      <div className={s.cardHead}>
        <h2 id="mine-title" className={s.cardTitle}>
          {t("title")}
        </h2>
        <span className={s.tag}>
          <Lock aria-hidden="true" />
          {t("private")}
        </span>
      </div>
      <div className={s.cardBody}>
        <dl className={s.mine}>
          <div>
            <dt>{t("paid")}</dt>
            <dd>
              <Money value={p.paid} />
            </dd>
          </div>
          <div>
            <dt>{t("share")}</dt>
            <dd>
              <Money value={p.share} />
            </dd>
          </div>
          <div>
            <dt>{owes ? t("iOwe") : t("owedToMe")}</dt>
            <dd data-sign={owes ? "neg" : owed ? "pos" : undefined} className={s.mineNet}>
              <Money value={owes ? p.i_owe : p.owed_to_me} />
            </dd>
          </div>
          <div className={s.mineLine}>
            <dt>{t("settled")}</dt>
            <dd className={s.mineSmall}>
              {t("settledLine", {
                sent: formatMoney(p.settled_sent, locale),
                received: formatMoney(p.settled_received, locale),
              })}
            </dd>
          </div>
          {!isZero(p.pending_sent) || !isZero(p.pending_received) ? (
            <div className={s.mineLine}>
              <dt>{t("pending")}</dt>
              <dd className={s.mineSmall}>
                {t("settledLine", {
                  sent: formatMoney(p.pending_sent, locale),
                  received: formatMoney(p.pending_received, locale),
                })}
              </dd>
            </div>
          ) : null}
        </dl>
        <p className={s.note}>{t("shareNote")}</p>

        {review_.length > 0 ? (
          <div className={s.mineBox} data-tone="attention">
            <strong className={s.mineBoxTitle}>
              <TriangleAlert aria-hidden="true" />
              {t("review.title", { count: review_.length })}
            </strong>
            <ul className={s.payments}>
              {review_.map((l) => (
                <li key={l.id} className={s.suggestionRow}>
                  <span>{t(`review.${l.review.reason ?? "expense_changed"}`)}</span>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => {
                      setReview((prev) => ({ n: (prev?.n ?? 0) + 1, link: l }));
                      setOpen("review");
                    }}
                  >
                    {t("review.action")}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {dupeCount > 0 ? (
          <div className={s.mineBox} data-tone="attention">
            <strong className={s.mineBoxTitle}>
              <TriangleAlert aria-hidden="true" />
              {t("duplicates.title", { count: dupeCount })}
            </strong>
            <span className={s.note}>{t("duplicates.lead")}</span>
            <span>
              <Button size="sm" onClick={() => setDupesOpen(true)}>
                {t("duplicates.review")}
              </Button>
            </span>
          </div>
        ) : null}

        {linkable.length > 0 ? (
          <div className={s.mineBox}>
            <strong className={s.mineBoxTitle}>{t("toRecord.title")}</strong>
            <ul className={s.payments}>
              {linkable.map((item) => (
                <li key={item.expense_id ?? item.settlement_id} className={s.suggestionRow}>
                  <span>
                    <span className={s.paymentLine} dir="auto">
                      {t(`toRecord.${item.kind}`, {
                        what: isolate(item.description ?? ""),
                      })}
                    </span>{" "}
                    <span className={s.note}>
                      <Money value={item.base_amount} />
                      {" · "}
                      <bdi>{formatDate(`${item.date}T12:00:00Z`, locale, "UTC")}</bdi>
                    </span>
                  </span>
                  <Button
                    size="sm"
                    onClick={() => {
                      setRecord((prev) => ({ n: (prev?.n ?? 0) + 1, item }));
                      setOpen("record");
                    }}
                  >
                    {t("toRecord.action")}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {links.length > 0 ? (
          <div className={s.mineBox}>
            <strong className={s.mineBoxTitle}>{t("recorded.title")}</strong>
            <ul className={s.payments}>
              {links.map((l) => (
                <li key={l.id} className={s.suggestionRow}>
                  <span>
                    <span className={s.paymentLine}>{t(`recorded.${l.kind}`)}</span>{" "}
                    <span className={s.note}>
                      <Money value={l.transaction.amount} />
                      {" · "}
                      <RecordedAccount link={l} />
                    </span>
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setUnlink((prev) => ({ n: (prev?.n ?? 0) + 1, link: l }));
                      setOpen("unlink");
                    }}
                  >
                    {t("recorded.unlink")}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {unrecorded ? (
          <p className={s.note} role="note">
            {isNegative(p.unrecorded)
              ? t("unrecorded.received", { amount: abs(p.unrecorded) })
              : t("unrecorded.paid", { amount: abs(p.unrecorded) })}
          </p>
        ) : p.links > 0 ? (
          <p className={s.note}>{t("unrecorded.none")}</p>
        ) : null}
      </div>

      {record ? (
        <RecordDialog
          key={`record-${record.n}`}
          trip={trip}
          item={record.item}
          duplicates={duplicates.find((g) => g.expense_id === record.item.expense_id) ?? null}
          open={open === "record"}
          onClose={() => setOpen(null)}
          onDone={done}
        />
      ) : null}
      {review ? (
        <ReviewDialog
          key={`review-${review.n}`}
          link={review.link}
          open={open === "review"}
          onClose={() => setOpen(null)}
          onDone={done}
        />
      ) : null}
      {unlink ? (
        <UnlinkDialog
          key={`unlink-${unlink.n}`}
          link={unlink.link}
          open={open === "unlink"}
          onClose={() => setOpen(null)}
          onDone={done}
        />
      ) : null}
      <DuplicatesDialog
        trip={trip}
        groups={duplicates}
        open={dupesOpen}
        onClose={() => setDupesOpen(false)}
        onDone={done}
      />
    </section>
  );
}

function RecordedAccount({ link }: { link: Link }) {
  const accountName = useAccountName();
  return <bdi>{link.transaction.account ? accountName(link.transaction.account) : ""}</bdi>;
}

// --- Record in my accounts

function RecordDialog({
  trip,
  item,
  duplicates,
  open,
  onClose,
  onDone,
}: {
  trip: Trip;
  item: Linkable;
  duplicates: DuplicateGroup | null;
  open: boolean;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const t = useTranslations("app.trips.mine.record");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, currencies } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const active = accounts.filter((a) => a.status === "active");
  const sameCurrency = active.find((a) => a.currency === item.base_amount.currency);
  const [accountId, setAccountId] = useState(sameCurrency?.id ?? active[0]?.id ?? "");
  const account = active.find((a) => a.id === accountId) ?? null;
  const exponent = currencies.find((c) => c.code === account?.currency)?.exponent ?? 2;
  const matches = item.base_amount.currency === account?.currency;
  const [amount, setAmount] = useState(
    matches ? editableAmount(item.base_amount.amount, locale) : "",
  );
  const [estimate, setEstimate] = useState<{ for: string; text: string | null } | null>(null);
  const [date, setDate] = useState(item.date);
  const candidates = item.kind === "expense_payment" ? (duplicates?.candidates ?? []) : [];
  const [mode, setMode] = useState<"create" | string>("create");
  const [confirmed, setConfirmed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const parsed = amount.trim() ? parseAmount(amount, locale, exponent) : null;
  const attachTo = candidates.find((c) => c.transaction.id === mode)?.transaction ?? null;

  // Another currency: offer the stored reference rate's result as a starting point only.
  useEffect(() => {
    if (!account || matches) return;
    let cancelled = false;
    fxApi
      .convert({
        from: item.base_amount.currency,
        to: account.currency,
        amount: item.base_amount.amount,
        date: item.date,
      })
      .then((result) => {
        if (cancelled) return;
        if (result.converted && result.rate) {
          setAmount(editableAmount(result.converted.amount, locale));
          setEstimate({
            for: account.id,
            text: t("estimate", {
              rate: `1 ${result.rate.base} = ${result.rate.rate} ${result.rate.quote}`,
              date: formatDate(`${result.rate.rate_date}T12:00:00Z`, locale, "UTC"),
            }),
          });
        } else setEstimate({ for: account.id, text: null });
      })
      .catch(() => {
        if (!cancelled) setEstimate({ for: account.id, text: null });
      });
    return () => {
      cancelled = true;
    };
  }, [account, matches, item, locale, t]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    const body = {
      trip_id: trip.id,
      expense_id: item.expense_id,
      settlement_id: item.settlement_id,
    };
    try {
      if (attachTo) {
        if (!confirmed) return;
        setBusy(true);
        await linksApi.create(
          {
            ...body,
            mode: "attach",
            transaction_id: attachTo.id,
            transaction_version: attachTo.version,
            confirm: true,
          },
          key,
        );
      } else {
        if (!account || !parsed?.ok) return;
        setBusy(true);
        await linksApi.create(
          { ...body, account_id: account.id, amount: parsed.amount, effective_date: date },
          key,
        );
      }
      onDone(t("done"));
    } catch (error) {
      if (handleAuthError(error)) return;
      setFailure(
        error instanceof ApiError && error.code === "already_linked"
          ? t("already")
          : message(error),
      );
    } finally {
      setBusy(false);
    }
  };

  const shareLabel =
    item.kind === "settlement_received" ? t("leadReceived") : t(`lead_${item.kind}`);

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("title")}
      lead={shareLabel}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button
            type="submit"
            form={`${id}-form`}
            variant="primary"
            busy={busy}
            disabled={attachTo ? !confirmed : false}
          >
            {attachTo ? t("attach") : t("save")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        {active.length === 0 ? <Notice>{t("noAccounts")}</Notice> : null}
        {candidates.length > 0 ? (
          <fieldset className={s.choices}>
            <legend className={appStyles.label}>{t("existing")}</legend>
            <label className={s.option}>
              <input
                type="radio"
                name={`${id}-mode`}
                checked={mode === "create"}
                onChange={() => setMode("create")}
              />
              <span>{t("createNew")}</span>
            </label>
            {candidates.map((c) => (
              <label key={c.transaction.id} className={s.option}>
                <input
                  type="radio"
                  name={`${id}-mode`}
                  checked={mode === c.transaction.id}
                  onChange={() => {
                    setMode(c.transaction.id);
                    setConfirmed(false);
                  }}
                />
                <span>
                  {t("useExisting", {
                    what: isolate(c.transaction.description ?? ""),
                    amount: isolate(formatMoney(c.transaction.amount, locale)),
                    date: formatDate(`${c.transaction.effective_date}T12:00:00Z`, locale, "UTC"),
                  })}
                  {c.strength === "strong" ? ` · ${t("strong")}` : ""}
                </span>
              </label>
            ))}
          </fieldset>
        ) : null}

        {attachTo ? (
          <div className={s.mineBox} data-tone="attention">
            <span>
              {t("attachExplain", {
                amount: isolate(formatMoney(attachTo.amount, locale)),
                share: isolate(formatMoney(duplicates?.my_share ?? item.base_amount, locale)),
              })}
            </span>
            <label className={s.option}>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>{t("attachConfirm")}</span>
            </label>
            {submitted && !confirmed ? (
              <span className={appStyles.error}>{t("attachConfirmRequired")}</span>
            ) : null}
          </div>
        ) : (
          <>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-account`}>
                {t("account")}
              </label>
              <Select
                id={`${id}-account`}
                value={accountId}
                onValueChange={(value) => {
                  const next = active.find((a) => a.id === value);
                  setAccountId(value);
                  setEstimate(null);
                  setAmount(
                    next?.currency === item.base_amount.currency
                      ? editableAmount(item.base_amount.amount, locale)
                      : "",
                  );
                }}
              >
                {active.map((a) => (
                  <option key={a.id} value={a.id}>
                    {`${accountName(a)} · ${a.currency}`}
                  </option>
                ))}
              </Select>
            </div>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-amount`}>
                {item.kind === "settlement_received"
                  ? t("amountReceived", { currency: account?.currency ?? "" })
                  : t("amountCharged", { currency: account?.currency ?? "" })}
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
                    example: formatMoney(
                      { amount: "1250.5", currency: account?.currency ?? "EUR" },
                      locale,
                      { display: "none" },
                    ),
                    currency: account?.currency ?? "",
                    count: exponent,
                  })}
                </span>
              ) : submitted && !parsed ? (
                <span className={appStyles.error}>{t("amountRequired")}</span>
              ) : (
                <span className={appStyles.hint}>
                  {matches
                    ? t("sameCurrencyHint")
                    : estimate?.for === account?.id && estimate?.text
                      ? estimate.text
                      : t("manualHint", {
                          amount: isolate(formatMoney(item.base_amount, locale)),
                        })}
                </span>
              )}
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
                max={todayIn(user.time_zone)}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
          </>
        )}
        <p className={s.note}>{t("privacy")}</p>
      </form>
    </AccountDialog>
  );
}

// --- Needs review

function ReviewDialog({
  link,
  open,
  onClose,
  onDone,
}: {
  link: Link;
  open: boolean;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const t = useTranslations("app.trips.mine.resolve");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const { currencies } = useFinance();
  const message = useFinanceError();
  const id = useId();
  const changed = link.review.reason === "expense_changed";
  const exponent =
    currencies.find((c) => c.code === link.transaction.amount.currency)?.exponent ?? 2;
  const suggested =
    changed &&
    link.review.current_base_amount &&
    link.review.current_base_amount.currency === link.transaction.amount.currency
      ? editableAmount(link.review.current_base_amount.amount, locale)
      : "";
  const [action, setAction] = useState<"update" | "keep" | "reverse">(changed ? "update" : "keep");
  const [amount, setAmount] = useState(suggested);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const parsed = amount.trim() ? parseAmount(amount, locale, exponent) : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailure(null);
    if (action === "update" && !parsed?.ok) return;
    setBusy(true);
    try {
      await linksApi.resolve(
        link.id,
        action === "update" && parsed?.ok ? { action, amount: parsed.amount } : { action },
        key,
      );
      onDone(t(`done.${action}`));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  const choices: ("update" | "keep" | "reverse")[] = changed
    ? ["update", "keep", "reverse"]
    : ["keep", "reverse"];

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("title")}
      lead={t(`lead.${link.review.reason ?? "expense_changed"}`, {
        was: isolate(formatMoney(link.linked_base_amount, locale)),
        now: isolate(
          link.review.current_base_amount
            ? formatMoney(link.review.current_base_amount, locale)
            : "",
        ),
      })}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {t("save")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        <fieldset className={s.choices}>
          <legend className={appStyles.label}>{t("choose")}</legend>
          {choices.map((c) => (
            <label key={c} className={s.option}>
              <input
                type="radio"
                name={`${id}-action`}
                checked={action === c}
                onChange={() => setAction(c)}
              />
              <span>
                <strong>{t(`${c}.label`)}</strong>
                <span className={s.note}> {t(`${c}.hint`)}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {action === "update" ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-amount`}>
              {t("amount", { currency: link.transaction.amount.currency })}
            </label>
            <MoneyInput
              id={`${id}-amount`}
              className={appStyles.input}
              value={amount}
              onValueChange={setAmount}
              aria-invalid={parsed && !parsed.ok ? true : undefined}
            />
            <span className={appStyles.hint}>{t("amountHint")}</span>
          </div>
        ) : null}
      </form>
    </AccountDialog>
  );
}

// --- Unlink

function UnlinkDialog({
  link,
  open,
  onClose,
  onDone,
}: {
  link: Link;
  open: boolean;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const t = useTranslations("app.trips.mine.unlink");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const id = useId();
  const [choice, setChoice] = useState<"reverse" | "convert">("reverse");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const plain = link.kind === "settlement_received" ? "income" : "expense";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    try {
      await linksApi.unlink(link.id, choice);
      onDone(t(`done.${choice}`));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
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
      title={t("title")}
      lead={t("lead", { amount: isolate(formatMoney(link.transaction.amount, locale)) })}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="danger" busy={busy}>
            {t("save")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        <fieldset className={s.choices}>
          <legend className={appStyles.label}>{t("choose")}</legend>
          {(["reverse", "convert"] as const).map((c) => (
            <label key={c} className={s.option}>
              <input
                type="radio"
                name={`${id}-choice`}
                checked={choice === c}
                onChange={() => setChoice(c)}
              />
              <span>
                <strong>{t(`${c}.label`)}</strong>
                <span className={s.note}>
                  {" "}
                  {c === "convert" ? t(`convert.${plain}`) : t("reverse.hint")}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      </form>
    </AccountDialog>
  );
}

// --- Possible duplicates

function DuplicatesDialog({
  trip,
  groups,
  open,
  onClose,
  onDone,
}: {
  trip: Trip;
  groups: DuplicateGroup[];
  open: boolean;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const t = useTranslations("app.trips.mine.duplicates");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [attach, setAttach] = useState<{ group: DuplicateGroup; txn: string } | null>(null);
  const [key] = useState(newIdempotencyKey);

  const dismiss = async (group: DuplicateGroup, txnId: string) => {
    setBusy(txnId);
    setFailure(null);
    try {
      await linksApi.dismiss(txnId, group.expense_id);
      onDone(t("dismissed"));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(null);
    }
  };

  const confirmAttach = async () => {
    if (!attach) return;
    const candidate = attach.group.candidates.find((c) => c.transaction.id === attach.txn);
    if (!candidate) return;
    setBusy(attach.txn);
    setFailure(null);
    try {
      await linksApi.create(
        {
          trip_id: trip.id,
          expense_id: attach.group.expense_id,
          mode: "attach",
          transaction_id: candidate.transaction.id,
          transaction_version: candidate.transaction.version,
          confirm: true,
        },
        key,
      );
      onDone(t("attached"));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("dialogTitle")}
      lead={t("dialogLead")}
      footer={
        <Button variant="primary" onClick={onClose}>
          {t("close")}
        </Button>
      }
    >
      <div className={appStyles.form}>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        {groups.map((g) => (
          <div key={g.expense_id} className={s.mineBox}>
            <strong className={s.paymentLine} dir="auto">
              {t("expenseLine", {
                what: isolate(g.description),
                amount: isolate(formatMoney(g.base_amount, locale)),
                share: isolate(formatMoney(g.my_share, locale)),
              })}
            </strong>
            <ul className={s.payments}>
              {g.candidates.map((c) => (
                <li key={c.transaction.id} className={s.payment}>
                  <span>
                    {t("candidate", {
                      what: isolate(c.transaction.description ?? ""),
                      amount: isolate(formatMoney(c.transaction.amount, locale)),
                      date: formatDate(`${c.transaction.effective_date}T12:00:00Z`, locale, "UTC"),
                    })}{" "}
                    <span
                      className={s.tag}
                      data-tone={c.strength === "strong" ? "accent" : undefined}
                    >
                      {t(`strength.${c.strength}`)}
                    </span>
                  </span>
                  {attach?.txn === c.transaction.id ? (
                    <div className={s.mineBox} data-tone="attention">
                      <span>
                        {t("attachExplain", {
                          amount: isolate(formatMoney(c.transaction.amount, locale)),
                          share: isolate(formatMoney(g.my_share, locale)),
                        })}
                      </span>
                      <span className={s.paymentActions}>
                        <Button size="sm" variant="ghost" onClick={() => setAttach(null)}>
                          {t("keep")}
                        </Button>
                        <Button
                          size="sm"
                          variant="primary"
                          busy={busy === c.transaction.id}
                          onClick={() => void confirmAttach()}
                        >
                          {t("attachConfirm")}
                        </Button>
                      </span>
                    </div>
                  ) : (
                    <span className={s.paymentActions}>
                      {g.paid_by_me ? (
                        <Button
                          size="sm"
                          onClick={() => setAttach({ group: g, txn: c.transaction.id })}
                        >
                          {t("attach")}
                        </Button>
                      ) : (
                        <span className={s.note}>{t("shareHint")}</span>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        busy={busy === c.transaction.id && !attach}
                        onClick={() => void dismiss(g, c.transaction.id)}
                      >
                        {t("notDuplicate")}
                      </Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </AccountDialog>
  );
}

/**
 * The payer approves an expense someone else recorded and records their own cash for it at once
 * (one transaction). Their account is touched only by their own approval; nobody else sees it.
 */
export function ApproveAndRecord({
  trip,
  versionId,
  amount,
  onDone,
}: {
  trip: Trip;
  versionId: string;
  amount: { amount: string; currency: string };
  onDone: (expense: Expense) => void;
}) {
  const t = useTranslations("app.trips.mine.approve");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const { accounts, currencies } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const active = accounts.filter((a) => a.status === "active");
  const [open, setOpen] = useState(false);
  const [accountId, setAccountId] = useState(
    active.find((a) => a.currency === amount.currency)?.id ?? active[0]?.id ?? "",
  );
  const currency = active.find((a) => a.id === accountId)?.currency ?? amount.currency;
  const [value, setValue] = useState(
    currency === amount.currency ? editableAmount(amount.amount, locale) : "",
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const parsed = value.trim()
    ? parseAmount(value, locale, currencies.find((c) => c.code === currency)?.exponent ?? 2)
    : null;
  if (active.length === 0) return null;
  if (!open) {
    return <Button onClick={() => setOpen(true)}>{t("open")}</Button>;
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailure(null);
    if (!parsed?.ok || !accountId) {
      setFailure(t("incomplete"));
      return;
    }
    setBusy(true);
    try {
      const result = await linksApi.approveWithLink(
        trip.id,
        versionId,
        { account_id: accountId, amount: parsed.amount },
        key,
      );
      onDone(result.expense);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className={s.mineBox} onSubmit={submit} noValidate aria-label={t("open")}>
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      <p className={s.note}>{t("lead")}</p>
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${id}-account`}>
          {t("account")}
        </label>
        <Select
          id={`${id}-account`}
          value={accountId}
          onValueChange={(value) => {
            setAccountId(value);
            const next = active.find((a) => a.id === value);
            setValue(
              next?.currency === amount.currency ? editableAmount(amount.amount, locale) : "",
            );
          }}
        >
          {active.map((a) => (
            <option key={a.id} value={a.id}>
              {`${accountName(a)} · ${a.currency}`}
            </option>
          ))}
        </Select>
      </div>
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor={`${id}-amount`}>
          {t("amount", { currency })}
        </label>
        <MoneyInput
          id={`${id}-amount`}
          className={appStyles.input}
          value={value}
          onValueChange={setValue}
          aria-invalid={parsed && !parsed.ok ? true : undefined}
        />
      </div>
      <div className={s.paymentActions}>
        <Button type="submit" variant="primary" busy={busy}>
          {t("submit")}
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
          {t("cancel")}
        </Button>
      </div>
    </form>
  );
}
