"use client";

import {
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Pencil,
  PiggyBank,
  Plus,
  RotateCcw,
  Archive,
  TriangleAlert,
  Wallet,
  Trash2,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import {
  addMonths,
  type ArchivedBudget,
  type Budget,
  type BudgetDetail,
  type BudgetMonth,
  budgetsApi,
  ratio,
} from "@/lib/analytics";
import type { Category } from "@/lib/finance";
import { formatDate, localeTag } from "@/lib/format";
import { editableAmount, formatMoney, type Money as MoneyValue, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import { AccountDialog, Money, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import {
  CategoryIcon,
  CategoryPicker,
  TransactionRow,
  useCategoryName,
  useFinanceError,
} from "../finance/parts";
import { TransactionDialog, useTxnDialog } from "../finance/transaction-dialog";
import { MoneyInput } from "../money-input";
import { Button, Notice, Spinner } from "../ui";
import s from "./analytics.module.css";
import { Select } from "../select";
import { ConfirmDialog } from "../confirm-dialog";
import { ApiError } from "@/lib/api";

/**
 * Monthly budgets (FINANCIAL_RULES.md §6). The API works out every figure: spent counts only
 * posted expenses in the budget's currency and category. Nothing is converted or added across
 * currencies here; bar lengths are drawing ratios only.
 */
export function BudgetsView() {
  return (
    <FinanceGate>
      <Budgets />
    </FinanceGate>
  );
}

export function useMonthName() {
  const locale = useLocale() as Locale;
  return (month: string, style: "long" | "short" = "long") =>
    new Intl.DateTimeFormat(localeTag(locale), {
      month: style,
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${month.slice(0, 7)}-15T12:00:00Z`));
}

export function usePercent() {
  const locale = useLocale() as Locale;
  // The API's own one-decimal text, shown in the reader's digits. Display only.
  return (value: string) =>
    new Intl.NumberFormat(localeTag(locale), { maximumFractionDigits: 1 }).format(Number(value));
}

const STATE_ICON = { normal: CircleCheck, approaching: TriangleAlert, exceeded: CircleAlert };

export function BudgetState({ budget }: { budget: Pick<Budget, "state" | "percent"> }) {
  const t = useTranslations("app.budgets");
  const percent = usePercent();
  const Icon = STATE_ICON[budget.state];
  return (
    <span className={s.state} data-state={budget.state}>
      <Icon aria-hidden="true" />
      {t(`state.${budget.state}`, { percent: percent(budget.percent) })}
    </span>
  );
}

export function BudgetMeter({ budget }: { budget: Budget }) {
  const fill = ratio(budget.spent.amount, budget.limit.amount);
  return (
    <span className={s.meter} aria-hidden="true">
      <span
        className={s.meterFill}
        data-state={budget.state}
        style={{ inlineSize: `${budget.state === "exceeded" ? 100 : Math.max(fill * 100, 1.5)}%` }}
      />
      <span className={s.meterTick} style={{ insetInlineStart: `${budget.alert_percent}%` }} />
    </span>
  );
}

function keyOf(b: { category: Category | null; currency: string }): string {
  return `${b.category?.id ?? "all"}:${b.currency}`;
}

type Editing = {
  n: number;
  category: Category | null;
  overall: boolean;
  currency: string | null;
  limit: MoneyValue | null;
  alert: number;
  locked: boolean;
};

function Budgets() {
  const t = useTranslations("app.budgets");
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const monthName = useMonthName();
  const thisMonth = todayIn(user.time_zone).slice(0, 7);
  const [month, setMonth] = useState(thisMonth);
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{ for: string; value?: BudgetMonth; failed?: boolean } | null>(
    null,
  );
  const [editing, setEditing] = useState<Editing | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [detail, setDetail] = useState<{ budget: Budget; n: number } | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const txnDialog = useTxnDialog();
  const request = `${month}|${revision}|${attempt}`;

  useEffect(() => {
    let cancelled = false;
    budgetsApi
      .month(month)
      .then((value) => {
        if (!cancelled) setData({ for: request, value });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setData({ for: request, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [request, month, handleAuthError]);

  const value = data?.value;
  const stale = data !== null && data.for !== request;
  const reload = () => setAttempt((n) => n + 1);
  const edit = (next: Omit<Editing, "n">) => {
    setEditing((prev) => ({ ...next, n: (prev?.n ?? 0) + 1 }));
    setEditOpen(true);
  };
  const create = () =>
    edit({
      category: null,
      overall: false,
      currency: null,
      limit: null,
      alert: 80,
      locked: false,
    });

  return (
    <div className={s.page}>
      <div className={s.head}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <div className={s.headActions}>
          <div className={s.monthNav} role="group" aria-label={t("monthPicker")}>
            <button
              type="button"
              aria-label={t("previousMonth")}
              onClick={() => setMonth((m) => addMonths(m, -1))}
            >
              <ChevronLeft aria-hidden="true" className={s.flipRtl} />
            </button>
            <span className={s.monthLabel} aria-live="polite">
              {monthName(month)}
            </span>
            <button
              type="button"
              aria-label={t("nextMonth")}
              onClick={() => setMonth((m) => addMonths(m, 1))}
            >
              <ChevronRight aria-hidden="true" className={s.flipRtl} />
            </button>
          </div>
          {month !== thisMonth ? (
            <Button size="sm" variant="ghost" onClick={() => setMonth(thisMonth)}>
              {t("thisMonth")}
            </Button>
          ) : null}
          <Button variant="primary" onClick={create}>
            <Plus aria-hidden="true" />
            {t("add")}
          </Button>
        </div>
      </div>

      {notice ? <Notice tone="success">{notice}</Notice> : null}

      {data?.failed ? (
        <Notice tone="error">
          {t("loadFailed")}{" "}
          <button type="button" className={appStyles.linkBtn} onClick={reload}>
            {t("retry")}
          </button>
        </Notice>
      ) : !value ? (
        <p className={appStyles.muted} role="status">
          <Spinner /> <span>{t("loading")}</span>
        </p>
      ) : (
        <div className={cn(stale && fs.refreshing)} aria-busy={stale || undefined}>
          {value.budgets.length === 0 ? (
            <div className={s.empty}>
              <PiggyBank aria-hidden="true" />
              <p className={s.emptyTitle}>{t("emptyTitle")}</p>
              <p className={cn(appStyles.muted, appStyles.small)}>{t("emptyBody")}</p>
              <Button variant="primary" onClick={create}>
                <Plus aria-hidden="true" />
                {t("add")}
              </Button>
            </div>
          ) : (
            <ul className={s.list} aria-label={t("listLabel", { month: monthName(month) })}>
              {value.budgets.map((budget) => (
                <li key={keyOf(budget)}>
                  <BudgetRow
                    budget={budget}
                    onOpen={() => {
                      setDetail((prev) => ({ budget, n: (prev?.n ?? 0) + 1 }));
                      setDetailOpen(true);
                    }}
                  />
                </li>
              ))}
            </ul>
          )}
          <p className={cn(s.note, s.gapAbove)}>{t("rules")}</p>

          {value.archived.length > 0 ? (
            <ArchivedList
              items={value.archived}
              onRestore={(item) =>
                edit({
                  category: item.category,
                  overall: item.category === null,
                  currency: item.currency,
                  limit: item.last_limit,
                  alert: 80,
                  locked: true,
                })
              }
            />
          ) : null}
        </div>
      )}

      {editing ? (
        <BudgetDialog
          key={`edit-${editing.n}`}
          editing={editing}
          month={month}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onSaved={(text) => {
            setEditOpen(false);
            setNotice(text);
            reload();
          }}
        />
      ) : null}
      {detail ? (
        <DetailDialog
          key={`detail-${detail.n}`}
          budget={detail.budget}
          month={month}
          open={detailOpen}
          onClose={() => setDetailOpen(false)}
          onEdit={(budget) => {
            setDetailOpen(false);
            edit({
              category: budget.category,
              overall: budget.category === null,
              currency: budget.currency,
              limit: budget.limit,
              alert: budget.alert_percent,
              locked: true,
            });
          }}
          onArchived={(text) => {
            setDetailOpen(false);
            setNotice(text);
            reload();
          }}
          onOpenTransaction={(id) => {
            setDetailOpen(false);
            txnDialog.show(id);
          }}
        />
      ) : null}
      <TransactionDialog dialog={txnDialog} />
    </div>
  );
}

export function BudgetName({ budget }: { budget: Pick<Budget, "category"> }) {
  const t = useTranslations("app.budgets");
  const categoryName = useCategoryName();
  return <>{budget.category ? categoryName(budget.category) : t("overall")}</>;
}

export function BudgetIcon({
  category,
  variant = "tile",
}: {
  category: Category | null;
  variant?: "tile" | "bare";
}) {
  return category ? (
    <CategoryIcon category={category} variant={variant} />
  ) : (
    <span className={s.overallIcon}>
      <Wallet aria-hidden="true" />
    </span>
  );
}

function BudgetRow({ budget, onOpen }: { budget: Budget; onOpen: () => void }) {
  const t = useTranslations("app.budgets");
  const locale = useLocale() as Locale;
  const over = budget.state === "exceeded";
  const remaining = over
    ? formatMoney(
        { ...budget.remaining, amount: budget.remaining.amount.replace(/^-/, "") },
        locale,
      )
    : formatMoney(budget.remaining, locale);
  return (
    <button type="button" className={s.budgetRow} onClick={onOpen}>
      <span className={s.rowIcon}>
        <BudgetIcon category={budget.category} />
      </span>
      <span className={s.rowName}>
        <span className={s.rowTitle} dir="auto">
          <BudgetName budget={budget} />
        </span>
        <span className={s.rowSub}>
          <bdi dir="ltr">{budget.currency}</bdi>
          {budget.not_counted.length > 0 ? (
            <>
              {" · "}
              {t("notCountedShort", { count: budget.not_counted.length })}
            </>
          ) : null}
        </span>
      </span>
      <span className={s.rowFigures}>
        <span className={s.spentOf}>
          {t.rich("spentOf", {
            spent: () => (
              <strong>
                <Money value={budget.spent} />
              </strong>
            ),
            limit: () => <Money value={budget.limit} />,
          })}
        </span>
        <BudgetState budget={budget} />
      </span>
      <span className={s.rowMeter}>
        <BudgetMeter budget={budget} />
        <span className={s.left} data-state={budget.state}>
          {over
            ? t.rich("over", { amount: () => <bdi dir="ltr">{remaining}</bdi> })
            : t.rich("remains", { amount: () => <bdi dir="ltr">{remaining}</bdi> })}
        </span>
      </span>
    </button>
  );
}

function ArchivedList({
  items,
  onRestore,
}: {
  items: ArchivedBudget[];
  onRestore: (item: ArchivedBudget) => void;
}) {
  const t = useTranslations("app.budgets");
  const monthName = useMonthName();
  return (
    <section aria-labelledby="archived-title" className={s.gapAbove}>
      <h2 id="archived-title" className={s.sectionTitle}>
        {t("archived.title")}
      </h2>
      <p className={cn(s.note, fs.gapBelow)}>{t("archived.lead")}</p>
      <ul className={s.list}>
        {items.map((item) => (
          <li key={keyOf(item)} className={s.archivedRow}>
            <BudgetIcon category={item.category} />
            <span className={s.rowName}>
              <span className={s.rowTitle} dir="auto">
                <BudgetName budget={item} />
              </span>
              <span className={s.rowSub}>
                {item.last_limit
                  ? t.rich("archived.was", {
                      limit: () => <Money value={item.last_limit as MoneyValue} />,
                      month: monthName(item.ended_from),
                    })
                  : t("archived.from", { month: monthName(item.ended_from) })}
              </span>
            </span>
            <Button size="sm" onClick={() => onRestore(item)}>
              <RotateCcw aria-hidden="true" />
              {t("archived.restore")}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function BudgetDialog({
  editing,
  month,
  open,
  onClose,
  onSaved,
}: {
  editing: Editing;
  month: string;
  open: boolean;
  onClose: () => void;
  onSaved: (notice: string) => void;
}) {
  const t = useTranslations("app.budgets.form");
  const amountT = useTranslations("app.accounts.amount");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const user = useUser();
  const { accounts, categories, currencies, defaultAccountId } = useFinance();
  const message = useFinanceError();
  const categoryName = useCategoryName();
  const monthName = useMonthName();
  const id = useId();
  const exponentOf = (code: string) => currencies.find((c) => c.code === code)?.exponent ?? 2;
  const defaultCurrency =
    accounts.find((a) => a.id === defaultAccountId)?.currency ??
    accounts[0]?.currency ??
    user.preferred_currency ??
    "USD";

  const [overall, setOverall] = useState(editing.overall);
  const [categoryId, setCategoryId] = useState<string | null>(editing.category?.id ?? null);
  const [currency, setCurrency] = useState(editing.currency ?? defaultCurrency);
  const [amount, setAmount] = useState(
    editing.limit ? editableAmount(editing.limit.amount, locale) : "",
  );
  const [fromMonth, setFromMonth] = useState(month);
  const [alert, setAlert] = useState(String(editing.alert));
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const expense = categories.filter((c) => c.kind === "expense" && c.status === "active");
  const parsed = amount.trim() ? parseAmount(amount, locale, exponentOf(currency)) : null;
  const alertNumber = Number(alert);
  const alertOk = /^\d{1,3}$/.test(alert) && alertNumber >= 1 && alertNumber <= 100;
  const monthOk = /^\d{4}-\d{2}$/.test(fromMonth);
  const codes = [...new Set([currency, ...accounts.map((a) => a.currency)])];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if ((!overall && !categoryId) || !parsed?.ok || !alertOk || !monthOk) return;
    setBusy(true);
    try {
      await budgetsApi.set({
        category_id: overall ? null : categoryId,
        limit: { amount: parsed.amount, currency },
        from_month: fromMonth,
        alert_percent: alertNumber,
      });
      const category = expense.find((c) => c.id === categoryId) ?? editing.category;
      onSaved(
        t("saved", {
          name: overall || !category ? t("overallName") : categoryName(category),
          month: monthName(fromMonth),
        }),
      );
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
      title={editing.locked ? t("editTitle") : t("title")}
      lead={t("lead")}
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
        {editing.locked ? (
          <p className={appStyles.muted}>
            <strong dir="auto">
              {editing.overall || !editing.category
                ? t("overallName")
                : categoryName(editing.category)}
            </strong>
            {" · "}
            <bdi dir="ltr">{currency}</bdi>
          </p>
        ) : (
          <>
            <div className={fs.segmented} role="group" aria-label={t("scope")}>
              <button
                type="button"
                className={fs.segment}
                aria-pressed={!overall}
                onClick={() => setOverall(false)}
              >
                {t("oneCategory")}
              </button>
              <button
                type="button"
                className={fs.segment}
                aria-pressed={overall}
                onClick={() => setOverall(true)}
              >
                {t("allSpending")}
              </button>
            </div>
            {!overall ? (
              <div className={appStyles.field}>
                <span className={appStyles.label} id={`${id}-cat-label`}>
                  {t("category")}
                </span>
                <CategoryPicker
                  categories={expense}
                  value={categoryId}
                  onChange={setCategoryId}
                  name={`${id}-cat`}
                  legend={t("category")}
                  invalid={submitted && !categoryId}
                  describedBy={submitted && !categoryId ? `${id}-cat-error` : undefined}
                />
                {submitted && !categoryId ? (
                  <span id={`${id}-cat-error`} className={appStyles.error}>
                    {t("categoryRequired")}
                  </span>
                ) : null}
              </div>
            ) : (
              <p className={cn(appStyles.hint)}>{t("allSpendingHint")}</p>
            )}
          </>
        )}

        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {t("limit")}
          </label>
          <div className={fs.inlineMoney}>
            <Select
              size="sm"
              className={fs.currencySelect}
              aria-label={t("currency")}
              value={currency}
              disabled={editing.locked}
              onValueChange={setCurrency}
            >
              {codes.map((code) => (
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
              aria-describedby={`${id}-amount-hint`}
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
            <span className={appStyles.error}>{t("limitRequired")}</span>
          ) : (
            <span id={`${id}-amount-hint`} className={appStyles.hint}>
              {t("currencyHint")}
            </span>
          )}
        </div>

        <div className={fs.fieldsRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-from`}>
              {t("from")}
            </label>
            <input
              id={`${id}-from`}
              type="month"
              className={appStyles.input}
              value={fromMonth}
              onChange={(e) => setFromMonth(e.target.value)}
              aria-invalid={submitted && !monthOk ? true : undefined}
            />
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-alert`}>
              {t("alert")}
            </label>
            <input
              id={`${id}-alert`}
              className={appStyles.input}
              inputMode="numeric"
              dir="ltr"
              maxLength={3}
              value={alert}
              onChange={(e) => setAlert(e.target.value.replace(/[^\d]/g, ""))}
              aria-invalid={submitted && !alertOk ? true : undefined}
              aria-describedby={`${id}-alert-hint`}
            />
            <span id={`${id}-alert-hint`} className={appStyles.hint}>
              {submitted && !alertOk ? t("alertInvalid") : t("alertHint")}
            </span>
          </div>
        </div>
        <p className={appStyles.hint}>{t("fromHint", { month: monthName(fromMonth || month) })}</p>
      </form>
    </AccountDialog>
  );
}

function DetailDialog({
  budget,
  month,
  open,
  onClose,
  onEdit,
  onArchived,
  onOpenTransaction,
}: {
  budget: Budget;
  month: string;
  open: boolean;
  onClose: () => void;
  onEdit: (budget: Budget) => void;
  onArchived: (notice: string) => void;
  onOpenTransaction: (id: string) => void;
}) {
  const t = useTranslations("app.budgets.detail");
  const locale = useLocale() as Locale;
  const bt = useTranslations("app.budgets");
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const monthName = useMonthName();
  const percent = usePercent();
  const categoryName = useCategoryName();
  const [data, setData] = useState<BudgetDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    budgetsApi
      .detail({ category_id: budget.category?.id ?? null, currency: budget.currency, month })
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [budget, month, handleAuthError]);

  const current = data?.budget ?? budget;
  const name = budget.category ? categoryName(budget.category) : bt("overall");

  const archive = async () => {
    setBusy(true);
    setFailure(null);
    try {
      await budgetsApi.end({
        category_id: budget.category?.id ?? null,
        currency: budget.currency,
        from_month: month,
      });
      onArchived(t("archivedNotice", { name, month: monthName(month) }));
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
      title={name}
      lead={t("lead", { month: monthName(month), currency: budget.currency })}
      footer={
        <Button variant="ghost" onClick={onClose}>
          {t("close")}
        </Button>
      }
    >
      <div className={s.detailBody}>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        <dl className={s.detailStats}>
          <div>
            <dt>{t("spent")}</dt>
            <dd>
              <Money value={current.spent} />
            </dd>
          </div>
          <div>
            <dt>{t("limit")}</dt>
            <dd>
              <Money value={current.limit} />
            </dd>
          </div>
          <div>
            <dt>{current.state === "exceeded" ? t("over") : t("remaining")}</dt>
            <dd className={current.state === "exceeded" ? s.neg : undefined}>
              <Money
                value={{
                  ...current.remaining,
                  amount: current.remaining.amount.replace(/^-/, ""),
                }}
              />
            </dd>
          </div>
        </dl>
        <div className={s.rowMeter}>
          <BudgetMeter budget={current} />
          <BudgetState budget={current} />
        </div>
        <p className={cn(s.note, s.gapAbove)}>
          {t("alertAt", { percent: percent(String(current.alert_percent)) })}
        </p>
        {current.not_counted.length > 0 ? (
          <p className={s.note}>
            {t.rich("notCounted", {
              amounts: () => (
                <>
                  {current.not_counted.map((m, i) => (
                    <span key={m.currency}>
                      {i > 0 ? ", " : ""}
                      <Money value={m} />
                    </span>
                  ))}
                </>
              ),
            })}
          </p>
        ) : null}

        <div className={s.detailActions}>
          <Button size="sm" onClick={() => onEdit(current)}>
            <Pencil aria-hidden="true" />
            {t("edit")}
          </Button>
          {confirming ? (
            <>
              <Button size="sm" variant="danger" busy={busy} onClick={archive}>
                {t("confirmArchive", { month: monthName(month) })}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirming(false)}
                disabled={busy}
              >
                {t("keep")}
              </Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
                <Archive aria-hidden="true" />
                {t("archive")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDeleting(true)}>
                <Trash2 aria-hidden="true" />
                {t("delete")}
              </Button>
            </>
          )}
        </div>
        <ConfirmDialog
          open={deleting}
          onOpenChange={setDeleting}
          title={t("deleteTitle", { name })}
          body={<p>{t("deleteBody")}</p>}
          confirm={t("delete")}
          onConfirm={async () => {
            try {
              await budgetsApi.remove({
                category_id: budget.category?.id ?? null,
                currency: budget.currency,
              });
              onArchived(t("deletedNotice", { name }));
              return null;
            } catch (error) {
              if (handleAuthError(error)) return null;
              return error instanceof ApiError && error.code === "budget_has_history"
                ? t("hasHistory", { month: monthName(month) })
                : message(error);
            }
          }}
        />
        {confirming ? <p className={s.note}>{t("archiveHint")}</p> : null}

        {failed ? (
          <Notice tone="error">{t("loadFailed")}</Notice>
        ) : !data ? (
          <p className={appStyles.muted} role="status">
            <Spinner /> <span>{t("loading")}</span>
          </p>
        ) : (
          <>
            <h3 className={s.subhead}>{t("recent")}</h3>
            <div className={s.tableWrap}>
              <table className={s.table}>
                <thead>
                  <tr>
                    <th scope="col">{t("month")}</th>
                    <th scope="col" className={s.num}>
                      {t("limit")}
                    </th>
                    <th scope="col" className={s.num}>
                      {t("spent")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.months.map((row) => (
                    <tr key={row.month}>
                      <th scope="row">{monthName(row.month, "short")}</th>
                      <td className={s.num}>
                        {row.limit ? <Money value={row.limit} /> : <span>{t("noLimit")}</span>}
                      </td>
                      <td className={s.num}>
                        <Money value={row.spent} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3 className={s.subhead}>{t("history")}</h3>
            <ul className={cn(s.note, s.plainList)}>
              {data.history.map((row) => (
                <li key={row.from_month}>
                  {row.limit
                    ? t.rich("historyRow", {
                        month: monthName(row.from_month),
                        limit: () => <Money value={row.limit as MoneyValue} />,
                      })
                    : t("historyEnded", { month: monthName(row.from_month) })}
                </li>
              ))}
            </ul>

            <h3 className={s.subhead}>{t("transactions", { count: data.transactions.length })}</h3>
            {data.transactions.length === 0 ? (
              <p className={s.note}>{t("noTransactions")}</p>
            ) : (
              <ul className={fs.rows}>
                {data.transactions.map((txn) => (
                  <li key={txn.id}>
                    <TransactionRow
                      txn={txn}
                      onOpen={(item) => onOpenTransaction(item.id)}
                      showDate
                    />
                  </li>
                ))}
              </ul>
            )}
            {data.trip_shares?.length ? (
              <>
                <h3 className={s.subhead}>{t("tripShares", { count: data.trip_shares.length })}</h3>
                <p className={s.note}>{t("tripSharesNote")}</p>
                <ul className={s.shareRows}>
                  {data.trip_shares.map((share) => (
                    <li key={`${share.expense_id}-${share.expense_date}`}>
                      <span>
                        <span dir="auto">{share.description}</span>
                        <span className={s.note}>
                          {" · "}
                          <bdi>{share.trip_name}</bdi>
                          {" · "}
                          <bdi>{formatDate(`${share.expense_date}T12:00:00Z`, locale, "UTC")}</bdi>
                        </span>
                      </span>
                      <Money value={share.amount} />
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {data.excluded.length > 0 ? (
              <>
                <h3 className={s.subhead}>{t("excluded", { count: data.excluded.length })}</h3>
                <p className={s.note}>{t("excludedNote", { currency: budget.currency })}</p>
                <ul className={fs.rows}>
                  {data.excluded.map((txn) => (
                    <li key={txn.id}>
                      <TransactionRow
                        txn={txn}
                        onOpen={(item) => onOpenTransaction(item.id)}
                        showDate
                      />
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        )}
      </div>
    </AccountDialog>
  );
}
