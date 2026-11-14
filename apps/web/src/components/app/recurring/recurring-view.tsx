"use client";

import {
  Archive,
  CalendarClock,
  HandCoins,
  Pause,
  Pencil,
  Play,
  Plus,
  Square,
  Trash2,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import type { Kind } from "@/lib/finance";
import { localeTag } from "@/lib/format";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import {
  type Debt,
  type DebtDetail,
  type Frequency,
  type Occurrence,
  type Rule,
  type RuleBody,
  recurringApi,
} from "@/lib/recurring";
import { cn } from "@/lib/utils";
import { AccountDialog, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { CategoryPicker, TransactionRow, useAccountName, useFinanceError } from "../finance/parts";
import { TransactionDialog, useTxnDialog } from "../finance/transaction-dialog";
import { Button, Loading, Notice, Spinner, Tag } from "../ui";
import { ConfirmDialog, KindIcon, OccurrenceRow, useWindowText } from "./parts";
import s from "./recurring.module.css";
import { MoneyInput } from "../money-input";
import { Select } from "../select";
import { ConfirmDialog as ConfirmAction, useConfirm } from "../confirm-dialog";
import { DeleteOrArchive, useRemovalFailure } from "../removal";

export function RecurringView() {
  return (
    <FinanceGate>
      <Recurring />
    </FinanceGate>
  );
}

type Data = { rules: Rule[]; occurrences: Occurrence[]; debts: Debt[] };

function shift(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function Recurring() {
  const t = useTranslations("app.recurring");
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{ for: string; value?: Data; failed?: boolean } | null>(null);
  const [confirming, setConfirming] = useState<{ o: Occurrence; n: number } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [ruleTarget, setRuleTarget] = useState<{ rule: Rule | null; n: number } | null>(null);
  const [ruleOpen, setRuleOpen] = useState(false);
  const [debtOpen, setDebtOpen] = useState<{ n: number } | null>(null);
  const [debtDialogOpen, setDebtDialogOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const today = todayIn(user.time_zone);
  const request = `${revision}|${attempt}`;

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      recurringApi.rules(),
      recurringApi.occurrences({ from: shift(today, -62), to: shift(today, 35) }),
      recurringApi.debts(),
    ])
      .then(([rules, occurrences, debts]) => {
        if (!cancelled) setData({ for: request, value: { rules, occurrences, debts } });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setData({ for: request, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [request, today, handleAuthError]);

  const reload = () => setAttempt((n) => n + 1);
  const value = data?.value;
  const confirm = (o: Occurrence) => {
    setConfirming((prev) => ({ o, n: (prev?.n ?? 0) + 1 }));
    setConfirmOpen(true);
  };
  const editRule = (rule: Rule | null) => {
    setRuleTarget((prev) => ({ rule, n: (prev?.n ?? 0) + 1 }));
    setRuleOpen(true);
  };

  const open = (value?.occurrences ?? []).filter((o) => o.status === "pending");
  const answerNow = open.filter((o) => o.state !== "expected");
  const coming = open.filter((o) => o.state === "expected");
  const history = (value?.occurrences ?? []).filter((o) => o.status !== "pending").reverse();

  return (
    <>
      <div className={fs.historyHead}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <span className={s.headActions}>
          <Button
            onClick={() => {
              setDebtOpen((prev) => ({ n: (prev?.n ?? 0) + 1 }));
              setDebtDialogOpen(true);
            }}
          >
            <HandCoins aria-hidden="true" />
            {t("addDebt")}
          </Button>
          <Button variant="primary" onClick={() => editRule(null)}>
            <Plus aria-hidden="true" />
            {t("addRule")}
          </Button>
        </span>
      </div>

      {notice ? (
        <Notice tone="success" className={fs.gapBelow}>
          {notice}
        </Notice>
      ) : null}

      {data?.failed ? (
        <Notice tone="error">
          {t("loadFailed")}{" "}
          <button type="button" className={appStyles.linkBtn} onClick={reload}>
            {t("retry")}
          </button>
        </Notice>
      ) : !value ? (
        <Loading label={t("loading")} />
      ) : value.rules.length === 0 && value.debts.length === 0 ? (
        <section className={cn(fs.panel, fs.emptyState)}>
          <CalendarClock aria-hidden="true" className={s.emptyIcon} />
          <p className={fs.emptyTitle}>{t("empty.title")}</p>
          <p className={cn(appStyles.muted, appStyles.small)}>{t("empty.body")}</p>
          <Button variant="primary" size="sm" onClick={() => editRule(null)}>
            {t("addRule")}
          </Button>
        </section>
      ) : (
        <div className={s.layout}>
          <div className={s.main}>
            <Section
              title={t("sections.answer")}
              count={answerNow.length}
              empty={t("sections.answerEmpty")}
            >
              {answerNow.map((o) => (
                <OccurrenceRow key={o.id} occurrence={o} onConfirm={confirm} onAnswered={reload} />
              ))}
            </Section>
            <Section
              title={t("sections.coming")}
              count={coming.length}
              empty={t("sections.comingEmpty")}
            >
              {coming.map((o) => (
                <OccurrenceRow key={o.id} occurrence={o} onConfirm={confirm} onAnswered={reload} />
              ))}
            </Section>
            {history.length > 0 ? (
              <Section title={t("sections.history")} count={history.length}>
                {history.slice(0, 12).map((o) => (
                  <OccurrenceRow
                    key={o.id}
                    occurrence={o}
                    onConfirm={confirm}
                    onAnswered={reload}
                  />
                ))}
              </Section>
            ) : null}
          </div>
          <div className={s.side}>
            <section className={fs.panel} aria-labelledby="rules-title">
              <div className={fs.panelHead}>
                <h2 id="rules-title" className={fs.panelTitle}>
                  {t("sections.rules")}
                </h2>
              </div>
              <ul className={s.cards}>
                {value.rules.map((rule) => (
                  <RuleCard
                    key={rule.id}
                    rule={rule}
                    onEdit={() => editRule(rule)}
                    onChanged={reload}
                  />
                ))}
              </ul>
            </section>
            <DebtsPanel debts={value.debts} onChanged={reload} onNotice={setNotice} />
          </div>
        </div>
      )}

      {confirming ? (
        <ConfirmDialog
          key={`confirm-${confirming.n}`}
          occurrence={confirming.o}
          open={confirmOpen}
          onClose={() => setConfirmOpen(false)}
          onDone={() => {
            setConfirmOpen(false);
            setNotice(t("confirmed"));
            reload();
          }}
        />
      ) : null}
      {ruleTarget ? (
        <RuleDialog
          key={`rule-${ruleTarget.n}`}
          rule={ruleTarget.rule}
          debts={value?.debts ?? []}
          open={ruleOpen}
          onClose={() => setRuleOpen(false)}
          onSaved={(text) => {
            setRuleOpen(false);
            setNotice(text);
            reload();
          }}
        />
      ) : null}
      {debtOpen ? (
        <DebtDialog
          key={`debt-${debtOpen.n}`}
          open={debtDialogOpen}
          onClose={() => setDebtDialogOpen(false)}
          onSaved={() => {
            setDebtDialogOpen(false);
            setNotice(t("debts.created"));
            reload();
          }}
        />
      ) : null}
    </>
  );
}

function Section({
  title,
  count,
  empty,
  children,
}: {
  title: string;
  count: number;
  empty?: string;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section className={fs.panel} aria-labelledby={id}>
      <div className={fs.panelHead}>
        <h2 id={id} className={fs.panelTitle}>
          {title}
        </h2>
        {count > 0 ? <Tag>{count}</Tag> : null}
      </div>
      {count === 0 && empty ? (
        <p className={cn(fs.panelBody, appStyles.muted, appStyles.small)}>{empty}</p>
      ) : (
        <ul className={s.occurrences}>{children}</ul>
      )}
    </section>
  );
}

/** "Monthly, days 25–28", "Every two weeks", "Yearly, 14 March". */
function useScheduleText() {
  const t = useTranslations("app.recurring.schedule");
  const locale = useLocale() as Locale;
  return (
    rule: Pick<Rule, "frequency" | "window_start_day" | "window_end_day" | "month" | "start_date">,
  ) => {
    const days =
      rule.window_start_day === null
        ? ""
        : rule.window_start_day === rule.window_end_day
          ? t("day", { day: rule.window_start_day })
          : t("days", {
              from: rule.window_start_day,
              to: rule.window_end_day ?? rule.window_start_day,
            });
    if (rule.frequency === "monthly") return `${t("monthly")} · ${days}`;
    if (rule.frequency === "yearly") {
      const month = new Intl.DateTimeFormat(localeTag(locale), {
        month: "long",
        timeZone: "UTC",
      }).format(new Date(Date.UTC(2026, (rule.month ?? 1) - 1, 15)));
      return `${t("yearly")} · ${month} · ${days}`;
    }
    const weekday = new Intl.DateTimeFormat(localeTag(locale), {
      weekday: "long",
      timeZone: "UTC",
    }).format(new Date(`${rule.start_date}T12:00:00Z`));
    return `${t(rule.frequency)} · ${weekday}`;
  };
}

function RuleCard({
  rule,
  onEdit,
  onChanged,
}: {
  rule: Rule;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const t = useTranslations("app.recurring");
  const locale = useLocale() as Locale;
  const schedule = useScheduleText();
  const windowText = useWindowText();
  const accountName = useAccountName();
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmEl, ask] = useConfirm();
  const removalFailure = useRemovalFailure();

  const act = async (action: "pause" | "resume" | "end") => {
    if (
      action === "end" &&
      !(await ask({
        title: t("endTitle", { name: rule.name }),
        body: t("endBody"),
        confirm: t("end"),
      }))
    )
      return;
    setBusy(true);
    setFailure(null);
    try {
      await recurringApi.setStatus(rule.id, rule.version, action);
      onChanged();
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className={cn(s.ruleCard, rule.status !== "active" && s.ruleInactive)}>
      <div className={s.ruleTop}>
        <KindIcon kind={rule.kind} direction={rule.debt?.direction} />
        <span className={s.occText}>
          <span className={s.occName} dir="auto">
            {rule.name}
          </span>
          <span className={s.occMeta}>{schedule(rule)}</span>
        </span>
        <span className={s.occAmount}>
          {rule.amount ? (
            <bdi dir="ltr" className={appStyles.num}>
              {formatMoney(rule.amount, locale)}
            </bdi>
          ) : (
            <span className={appStyles.muted}>{t("variable")}</span>
          )}
        </span>
      </div>
      <p className={s.ruleMeta}>
        <bdi>{accountName(rule.account)}</bdi>
        {rule.total_occurrences ? ` · ${t("payments", { count: rule.total_occurrences })}` : ""}
        {rule.next
          ? ` · ${t("next", { when: windowText(rule.next.window_start, rule.next.window_end) })}`
          : ""}
        {rule.status !== "active" ? (
          <>
            {" · "}
            <Tag>{t(`ruleStatus.${rule.status}`)}</Tag>
          </>
        ) : null}
      </p>
      {rule.status !== "ended" ? (
        <div className={s.ruleActions}>
          <Button size="sm" variant="ghost" onClick={onEdit}>
            <Pencil aria-hidden="true" />
            {t("edit")}
          </Button>
          {rule.status === "active" ? (
            <Button size="sm" variant="ghost" busy={busy} onClick={() => void act("pause")}>
              <Pause aria-hidden="true" />
              {t("pause")}
            </Button>
          ) : (
            <Button size="sm" variant="ghost" busy={busy} onClick={() => void act("resume")}>
              <Play aria-hidden="true" />
              {t("resume")}
            </Button>
          )}
          <Button size="sm" variant="ghost" busy={busy} onClick={() => void act("end")}>
            <Square aria-hidden="true" />
            {t("end")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDeleting(true)}>
            <Trash2 aria-hidden="true" />
            {t("delete")}
          </Button>
        </div>
      ) : (
        <div className={s.ruleActions}>
          <Button size="sm" variant="ghost" onClick={() => setDeleting(true)}>
            <Trash2 aria-hidden="true" />
            {t("delete")}
          </Button>
        </div>
      )}
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      {confirmEl}
      <ConfirmAction
        open={deleting}
        onOpenChange={setDeleting}
        title={t("deleteTitle", { name: rule.name })}
        body={
          <>
            <p>{t("deleteBody")}</p>
            <DeleteOrArchive canArchive={false} />
          </>
        }
        confirm={t("delete")}
        onConfirm={async () => {
          try {
            await recurringApi.deleteRule(rule.id, rule.version);
            onChanged();
            return null;
          } catch (error) {
            return removalFailure(error);
          }
        }}
      />
    </li>
  );
}

const FREQUENCIES: Frequency[] = ["monthly", "weekly", "biweekly", "yearly"];
const DAYS = Array.from({ length: 31 }, (_, i) => i + 1);

function RuleDialog({
  rule,
  debts,
  open,
  onClose,
  onSaved,
}: {
  rule: Rule | null;
  debts: Debt[];
  open: boolean;
  onClose: () => void;
  onSaved: (text: string) => void;
}) {
  const t = useTranslations("app.recurring.form");
  const kinds = useTranslations("app.recurring.kinds");
  const freq = useTranslations("app.recurring.schedule");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, categories, currencies, defaultAccountId } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const today = todayIn(user.time_zone);
  const defaultAccount = accounts.find((a) => a.id === defaultAccountId) ?? accounts[0];
  const openDebts = debts.filter((d) => d.status === "active" && !d.settled);

  const [kind, setKind] = useState<Rule["kind"]>(rule?.kind ?? "income");
  const [name, setName] = useState(rule?.name ?? "");
  const [variable, setVariable] = useState(rule ? rule.amount === null : false);
  const [amount, setAmount] = useState(
    rule?.amount ? editableAmount(rule.amount.amount, locale) : "",
  );
  const [currency, setCurrency] = useState(rule?.currency ?? defaultAccount?.currency ?? "TRY");
  const [accountId, setAccountId] = useState(rule?.account.id ?? defaultAccount?.id ?? "");
  const [categoryId, setCategoryId] = useState<string | null>(rule?.category?.id ?? null);
  const [debtId, setDebtId] = useState(rule?.debt?.id ?? openDebts[0]?.id ?? "");
  const [frequency, setFrequency] = useState<Frequency>(rule?.frequency ?? "monthly");
  const [fromDay, setFromDay] = useState(rule?.window_start_day ?? 1);
  const [toDay, setToDay] = useState(rule?.window_end_day ?? rule?.window_start_day ?? 1);
  const [month, setMonth] = useState(rule?.month ?? 1);
  const [startDate, setStartDate] = useState(rule?.start_date ?? today);
  const [ending, setEnding] = useState<"never" | "count" | "date">(
    rule?.total_occurrences ? "count" : rule?.end_date ? "date" : "never",
  );
  const [count, setCount] = useState(String(rule?.total_occurrences ?? 12));
  const [endDate, setEndDate] = useState(rule?.end_date ?? "");
  const [reminders, setReminders] = useState(rule?.reminders ?? true);
  const [remindDays, setRemindDays] = useState(rule?.remind_days_before ?? 0);
  const [notes, setNotes] = useState(rule?.notes ?? "");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);

  const debt = debts.find((d) => d.id === debtId);
  const effectiveCurrency = kind === "debt_payment" && debt ? debt.currency : currency;
  const parsed =
    !variable && amount.trim() ? parseAmount(amount, locale, exponentOf(effectiveCurrency)) : null;
  const categoryKind: Kind | null = kind === "debt_payment" ? null : kind;
  const categoryOptions = categories.filter(
    (c) => c.kind === categoryKind && c.status === "active",
  );
  const monthly = frequency === "monthly" || frequency === "yearly";
  const countNumber = Number(count);
  const nameMissing = submitted && !name.trim();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!name.trim() || (!variable && !parsed?.ok) || !accountId) return;
    if (categoryKind && !categoryId) return;
    if (kind === "debt_payment" && !debt) return;
    if (
      ending === "count" &&
      !(countNumber >= 1 && countNumber <= 600 && Number.isInteger(countNumber))
    )
      return;
    if (ending === "date" && !endDate) return;
    const money =
      !variable && parsed?.ok ? { amount: parsed.amount, currency: effectiveCurrency } : null;
    setBusy(true);
    try {
      if (rule) {
        await recurringApi.updateRule(rule.id, rule.version, {
          name: name.trim(),
          notes: notes.trim() || null,
          amount: money,
          account_id: accountId,
          ...(categoryKind && categoryId ? { category_id: categoryId } : {}),
          ...(monthly
            ? { window_start_day: fromDay, window_end_day: Math.max(fromDay, toDay) }
            : {}),
          ...(frequency === "yearly" ? { month } : {}),
          end_date: ending === "date" ? endDate : null,
          total_occurrences: ending === "count" ? countNumber : null,
          reminders,
          remind_days_before: remindDays,
        });
        onSaved(t("saved"));
      } else {
        const body: RuleBody = {
          kind,
          name: name.trim(),
          notes: notes.trim() || null,
          amount: money,
          currency: effectiveCurrency,
          account_id: accountId,
          category_id: categoryKind ? categoryId : null,
          debt_id: kind === "debt_payment" ? debtId : null,
          frequency,
          window_start_day: monthly ? fromDay : null,
          window_end_day: monthly ? Math.max(fromDay, toDay) : null,
          month: frequency === "yearly" ? month : null,
          start_date: startDate,
          end_date: ending === "date" ? endDate : null,
          total_occurrences: ending === "count" ? countNumber : null,
          reminders,
          remind_days_before: remindDays,
        };
        await recurringApi.createRule(body, key);
        onSaved(t("created"));
      }
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  const monthName = (m: number) =>
    new Intl.DateTimeFormat(localeTag(locale), { month: "long", timeZone: "UTC" }).format(
      new Date(Date.UTC(2026, m - 1, 15)),
    );

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={rule ? t("editTitle") : t("createTitle")}
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {rule ? t("save") : t("create")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {rule ? null : (
          <div className={fs.segmented} role="group" aria-label={t("kind")}>
            {(["income", "expense", "debt_payment"] as const).map((k) => (
              <button
                key={k}
                type="button"
                className={fs.segment}
                data-kind={k === "debt_payment" ? "expense" : k}
                aria-pressed={kind === k}
                onClick={() => {
                  setKind(k);
                  setCategoryId(null);
                }}
                disabled={k === "debt_payment" && openDebts.length === 0}
              >
                {kinds(k)}
              </button>
            ))}
          </div>
        )}
        {kind === "debt_payment" && openDebts.length === 0 && !rule ? (
          <p className={appStyles.hint}>{t("needsDebt")}</p>
        ) : null}

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-name`}>
            {t("name")}
          </label>
          <input
            id={`${id}-name`}
            className={appStyles.input}
            dir="auto"
            maxLength={100}
            placeholder={t(`namePlaceholder.${kind}`)}
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-invalid={nameMissing || undefined}
          />
          {nameMissing ? <span className={appStyles.error}>{t("nameRequired")}</span> : null}
        </div>

        {kind === "debt_payment" && !rule ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-debt`}>
              {t("debt")}
            </label>
            <Select id={`${id}-debt`} value={debtId} onValueChange={setDebtId}>
              {openDebts.map((d) => (
                <option key={d.id} value={d.id}>
                  {`${d.counterparty} · ${formatMoney(d.remaining, locale)}`}
                </option>
              ))}
            </Select>
          </div>
        ) : null}

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {t("amount")}
          </label>
          <div className={fs.inlineMoney}>
            {kind === "debt_payment" || rule ? (
              <bdi dir="ltr" className={fs.amountCurrency}>
                {effectiveCurrency}
              </bdi>
            ) : (
              <Select
                size="sm"
                className={fs.currencySelect}
                aria-label={t("currency")}
                value={currency}
                dir="ltr"
                onValueChange={setCurrency}
              >
                {[
                  ...new Set([
                    currency,
                    ...accounts.map((a) => a.currency),
                    "USD",
                    "EUR",
                    "GBP",
                    "TRY",
                  ]),
                ].map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </Select>
            )}
            <MoneyInput
              id={`${id}-amount`}
              className={appStyles.input}
              value={variable ? "" : amount}
              disabled={variable}
              onValueChange={setAmount}
              aria-invalid={
                !variable && ((parsed && !parsed.ok) || (submitted && !parsed)) ? true : undefined
              }
            />
          </div>
          {!variable && parsed && !parsed.ok ? (
            <span className={appStyles.error}>
              {amountT(`errors.${parsed.error}`, {
                example: formatMoney({ amount: "1250.5", currency: effectiveCurrency }, locale, {
                  display: "none",
                }),
                currency: effectiveCurrency,
                count: exponentOf(effectiveCurrency),
              })}
            </span>
          ) : null}
          {kind === "debt_payment" ? null : (
            <label className={fs.checkRow}>
              <input
                type="checkbox"
                checked={variable}
                onChange={(e) => setVariable(e.target.checked)}
              />
              <span>{t("variable")}</span>
            </label>
          )}
        </div>

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-account`}>
            {kind === "income" ? t("into") : t("from")}
          </label>
          <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {`${accountName(a)} · ${a.currency}`}
              </option>
            ))}
          </Select>
        </div>

        {categoryKind ? (
          <div className={appStyles.field}>
            <span className={appStyles.label}>{t("category")}</span>
            <CategoryPicker
              name={`${id}-category`}
              legend={t("category")}
              categories={categoryOptions}
              value={categoryId}
              onChange={setCategoryId}
              invalid={submitted && !categoryId}
              limit={8}
            />
            {submitted && !categoryId ? (
              <span className={appStyles.error}>{t("categoryRequired")}</span>
            ) : null}
          </div>
        ) : null}

        <div className={fs.fieldsRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-freq`}>
              {t("frequency")}
            </label>
            <Select
              id={`${id}-freq`}
              value={frequency}
              disabled={Boolean(rule)}
              onValueChange={(value) => setFrequency(value as Frequency)}
            >
              {FREQUENCIES.map((f) => (
                <option key={f} value={f}>
                  {freq(f)}
                </option>
              ))}
            </Select>
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-start`}>
              {t("startDate")}
            </label>
            <input
              id={`${id}-start`}
              type="date"
              className={appStyles.input}
              value={startDate}
              disabled={Boolean(rule)}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
        </div>

        {monthly ? (
          <fieldset className={s.windowFields}>
            <legend className={appStyles.label}>{t("window")}</legend>
            {frequency === "yearly" ? (
              <Select
                aria-label={t("month")}
                value={month}
                onValueChange={(value) => setMonth(Number(value))}
              >
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                  <option key={m} value={m}>
                    {monthName(m)}
                  </option>
                ))}
              </Select>
            ) : null}
            <div className={s.windowDay}>
              <label htmlFor={`${id}-from-day`}>{t("fromDay")}</label>
              <Select
                id={`${id}-from-day`}
                value={fromDay}
                onValueChange={(value) => setFromDay(Number(value))}
              >
                {DAYS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </Select>
            </div>
            <div className={s.windowDay}>
              <label htmlFor={`${id}-to-day`}>{t("toDay")}</label>
              <Select
                id={`${id}-to-day`}
                value={Math.max(fromDay, toDay)}
                onValueChange={(value) => setToDay(Number(value))}
              >
                {DAYS.filter((d) => d >= fromDay).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </Select>
            </div>
            <p className={cn(appStyles.hint, s.windowHint)}>{t("windowHint")}</p>
          </fieldset>
        ) : (
          <p className={appStyles.hint}>{t("weeklyHint")}</p>
        )}

        <fieldset className={s.endFields}>
          <legend className={appStyles.label}>{t("ends")}</legend>
          {(["never", "count", "date"] as const).map((e) => (
            <label key={e} className={fs.methodOption}>
              <input
                type="radio"
                name={`${id}-end`}
                checked={ending === e}
                onChange={() => setEnding(e)}
              />
              <span>{t(`endsOptions.${e}`)}</span>
            </label>
          ))}
          {ending === "count" ? (
            <input
              className={appStyles.input}
              inputMode="numeric"
              aria-label={t("count")}
              value={count}
              onChange={(e) => setCount(e.target.value.replace(/[^0-9]/g, ""))}
            />
          ) : null}
          {ending === "date" ? (
            <input
              type="date"
              className={appStyles.input}
              aria-label={t("endDate")}
              value={endDate}
              min={startDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          ) : null}
        </fieldset>

        <div className={fs.fieldsRow}>
          <label className={fs.checkRow}>
            <input
              type="checkbox"
              checked={reminders}
              onChange={(e) => setReminders(e.target.checked)}
            />
            <span>{t("reminders")}</span>
          </label>
          {reminders ? (
            <Select
              aria-label={t("remindWhen")}
              value={remindDays}
              onValueChange={(value) => setRemindDays(Number(value))}
            >
              {[0, 1, 3, 7].map((d) => (
                <option key={d} value={d}>
                  {t("remindDays", { count: d })}
                </option>
              ))}
            </Select>
          ) : null}
        </div>

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-notes`}>
            {t("notes")}
          </label>
          <textarea
            id={`${id}-notes`}
            className={cn(appStyles.input, fs.note)}
            dir="auto"
            rows={2}
            maxLength={500}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        <p className={fs.convertFine}>{t("expectedOnly")}</p>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </form>
    </AccountDialog>
  );
}

// --- Debts

function DebtsPanel({
  debts,
  onChanged,
  onNotice,
}: {
  debts: Debt[];
  onChanged: () => void;
  onNotice: (text: string) => void;
}) {
  const t = useTranslations("app.recurring.debts");
  const [paying, setPaying] = useState<{ debt: Debt; n: number } | null>(null);
  const [payOpen, setPayOpen] = useState(false);
  return (
    <section className={fs.panel} aria-labelledby="debts-title">
      <div className={fs.panelHead}>
        <h2 id="debts-title" className={fs.panelTitle}>
          {t("title")}
        </h2>
      </div>
      {debts.length === 0 ? (
        <p className={cn(fs.panelBody, appStyles.muted, appStyles.small)}>{t("empty")}</p>
      ) : (
        <ul className={s.cards}>
          {debts.map((debt) => (
            <DebtCard
              key={debt.id}
              debt={debt}
              onPay={() => {
                setPaying((prev) => ({ debt, n: (prev?.n ?? 0) + 1 }));
                setPayOpen(true);
              }}
              onChanged={onChanged}
            />
          ))}
        </ul>
      )}
      <p className={cn(fs.panelBody, fs.convertFine)}>{t("note")}</p>
      {paying ? (
        <PaymentDialog
          key={paying.n}
          debt={paying.debt}
          open={payOpen}
          onClose={() => setPayOpen(false)}
          onSaved={() => {
            setPayOpen(false);
            onNotice(t("paid"));
            onChanged();
          }}
        />
      ) : null}
    </section>
  );
}

function DebtCard({
  debt,
  onPay,
  onChanged,
}: {
  debt: Debt;
  onPay: () => void;
  onChanged: () => void;
}) {
  const t = useTranslations("app.recurring.debts");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [detail, setDetail] = useState<DebtDetail | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const dialog = useTxnDialog();
  // Bar length only: a ratio for drawing, never a displayed amount.
  const share =
    Number(debt.original.amount) > 0
      ? Number(debt.repaid.amount) / Number(debt.original.amount)
      : 0;

  const toggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next) {
      try {
        setDetail(await recurringApi.debt(debt.id));
      } catch (error) {
        if (!handleAuthError(error)) setFailure(message(error));
      }
    }
  };

  const archive = async () => {
    try {
      await recurringApi.archiveDebt(debt.id, debt.version, debt.status !== "archived");
      onChanged();
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    }
  };

  return (
    <li className={cn(s.ruleCard, debt.status === "archived" && s.ruleInactive)}>
      <div className={s.ruleTop}>
        <KindIcon kind="debt_payment" direction={debt.direction} />
        <span className={s.occText}>
          <span className={s.occName} dir="auto">
            {debt.counterparty}
          </span>
          <span className={s.occMeta}>
            {debt.direction === "i_owe" ? t("iOwe") : t("owedToMe")}
          </span>
        </span>
        <span className={s.occAmount}>
          <bdi dir="ltr" className={appStyles.num}>
            {formatMoney(debt.remaining, locale)}
          </bdi>
          <span className={s.occExpected}>
            {t("of", { amount: formatMoney(debt.original, locale) })}
          </span>
        </span>
      </div>
      <span className={s.progress} aria-hidden="true">
        <span style={{ inlineSize: `${Math.min(100, Math.max(0, share * 100))}%` }} />
      </span>
      <div className={s.ruleActions}>
        {debt.settled ? (
          <Tag tone="positive">{t("settled")}</Tag>
        ) : debt.status === "active" ? (
          <Button size="sm" onClick={onPay}>
            {debt.direction === "i_owe" ? t("recordRepayment") : t("recordReceived")}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={() => void toggle()}>
          {t("history")}
        </Button>
        {debt.settled ? (
          <Button size="sm" variant="ghost" onClick={() => void archive()}>
            <Archive aria-hidden="true" />
            {debt.status === "archived" ? t("restore") : t("archive")}
          </Button>
        ) : null}
      </div>
      {expanded ? (
        detail ? (
          <ul className={fs.rows}>
            {detail.history.map((txn) => (
              <li key={txn.id}>
                <TransactionRow txn={txn} onOpen={(item) => dialog.show(item.id)} showDate />
              </li>
            ))}
          </ul>
        ) : (
          <Spinner />
        )
      ) : null}
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      <TransactionDialog dialog={dialog} />
    </li>
  );
}

function DebtDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useTranslations("app.recurring.debts.form");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, currencies, defaultAccountId, changed } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const defaultAccount = accounts.find((a) => a.id === defaultAccountId) ?? accounts[0];
  const today = todayIn(user.time_zone);

  const [direction, setDirection] = useState<Debt["direction"]>("i_owe");
  const [counterparty, setCounterparty] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(defaultAccount?.currency ?? "TRY");
  const [moved, setMoved] = useState(true);
  const [accountId, setAccountId] = useState(defaultAccount?.id ?? "");
  const [accountAmount, setAccountAmount] = useState("");
  const [startDate, setStartDate] = useState(today);
  const [notes, setNotes] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);

  const account = accounts.find((a) => a.id === accountId);
  const parsed = amount.trim() ? parseAmount(amount, locale, exponentOf(currency)) : null;
  const cross = moved && account && account.currency !== currency;
  const accountParsed =
    cross && accountAmount.trim() && account
      ? parseAmount(accountAmount, locale, exponentOf(account.currency))
      : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!counterparty.trim() || !parsed?.ok || (moved && !account) || (cross && !accountParsed?.ok))
      return;
    setBusy(true);
    try {
      await recurringApi.createDebt(
        {
          direction,
          counterparty: counterparty.trim(),
          amount: { amount: parsed.amount, currency },
          start_date: startDate,
          notes: notes.trim() || null,
          account_id: moved && account ? account.id : null,
          ...(cross && accountParsed?.ok ? { account_amount: accountParsed.amount } : {}),
        },
        key,
      );
      await changed();
      onSaved();
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
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {t("create")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        <div className={fs.segmented} role="group" aria-label={t("direction")}>
          {(["i_owe", "owed_to_me"] as const).map((d) => (
            <button
              key={d}
              type="button"
              className={fs.segment}
              aria-pressed={direction === d}
              onClick={() => setDirection(d)}
            >
              {t(`directions.${d}`)}
            </button>
          ))}
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-who`}>
            {direction === "i_owe" ? t("lender") : t("borrower")}
          </label>
          <input
            id={`${id}-who`}
            className={appStyles.input}
            dir="auto"
            maxLength={100}
            value={counterparty}
            onChange={(e) => setCounterparty(e.target.value)}
            aria-invalid={submitted && !counterparty.trim() ? true : undefined}
          />
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
              dir="ltr"
              onValueChange={setCurrency}
            >
              {[
                ...new Set([
                  currency,
                  ...accounts.map((a) => a.currency),
                  "USD",
                  "EUR",
                  "GBP",
                  "TRY",
                ]),
              ].map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
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
                example: formatMoney({ amount: "1250.5", currency: currency }, locale, {
                  display: "none",
                }),
                currency,
                count: exponentOf(currency),
              })}
            </span>
          ) : null}
        </div>
        <fieldset className={s.endFields}>
          <legend className={appStyles.label}>{t("movedQuestion")}</legend>
          <label className={fs.methodOption}>
            <input
              type="radio"
              name={`${id}-moved`}
              checked={moved}
              onChange={() => setMoved(true)}
            />
            <span>{direction === "i_owe" ? t("movedIn") : t("movedOut")}</span>
          </label>
          <label className={fs.methodOption}>
            <input
              type="radio"
              name={`${id}-moved`}
              checked={!moved}
              onChange={() => setMoved(false)}
            />
            <span>{t("before")}</span>
          </label>
        </fieldset>
        {moved ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-account`}>
              {direction === "i_owe" ? t("into") : t("from")}
            </label>
            <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {`${accountName(a)} · ${a.currency}`}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        {cross && account ? (
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
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-date`}>
            {t("startDate")}
          </label>
          <input
            id={`${id}-date`}
            type="date"
            className={appStyles.input}
            value={startDate}
            max={today}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-notes`}>
            {t("notes")}
          </label>
          <textarea
            id={`${id}-notes`}
            className={cn(appStyles.input, fs.note)}
            dir="auto"
            rows={2}
            maxLength={500}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        <p className={fs.convertFine}>{t("noInterest")}</p>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </form>
    </AccountDialog>
  );
}

function PaymentDialog({
  debt,
  open,
  onClose,
  onSaved,
}: {
  debt: Debt;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useTranslations("app.recurring.debts.payment");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, currencies, defaultAccountId, changed } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const today = todayIn(user.time_zone);
  const [amount, setAmount] = useState("");
  const [accountId, setAccountId] = useState(
    (
      accounts.find((a) => a.currency === debt.currency) ??
      accounts.find((a) => a.id === defaultAccountId) ??
      accounts[0]
    )?.id ?? "",
  );
  const [accountAmount, setAccountAmount] = useState("");
  const [date, setDate] = useState(today);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const account = accounts.find((a) => a.id === accountId);
  const parsed = amount.trim() ? parseAmount(amount, locale, exponentOf(debt.currency)) : null;
  const cross = account && account.currency !== debt.currency;
  const accountParsed =
    cross && accountAmount.trim() && account
      ? parseAmount(accountAmount, locale, exponentOf(account.currency))
      : null;
  const owe = debt.direction === "i_owe";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!parsed?.ok || !account || (cross && !accountParsed?.ok)) return;
    setBusy(true);
    try {
      await recurringApi.pay(
        debt.id,
        {
          account_id: account.id,
          amount: { amount: parsed.amount, currency: debt.currency },
          effective_date: date,
          ...(cross && accountParsed?.ok ? { account_amount: accountParsed.amount } : {}),
        },
        key,
      );
      await changed();
      onSaved();
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
      title={
        owe
          ? t("titleRepay", { name: debt.counterparty })
          : t("titleReceive", { name: debt.counterparty })
      }
      lead={t("lead", { amount: formatMoney(debt.remaining, locale) })}
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
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {t("amount")}
          </label>
          <div className={fs.inlineMoney}>
            <bdi dir="ltr" className={fs.amountCurrency}>
              {debt.currency}
            </bdi>
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
                example: formatMoney({ amount: "1250.5", currency: debt.currency }, locale, {
                  display: "none",
                }),
                currency: debt.currency,
                count: exponentOf(debt.currency),
              })}
            </span>
          ) : null}
        </div>
        <div className={fs.fieldsRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-account`}>
              {owe ? t("from") : t("into")}
            </label>
            <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {`${accountName(a)} · ${a.currency}`}
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
              onChange={(e) => setDate(e.target.value)}
            />
          </div>
        </div>
        {cross && account ? (
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
        <p className={fs.convertFine}>{owe ? t("notSpending") : t("notIncome")}</p>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </form>
    </AccountDialog>
  );
}
