"use client";

import { CirclePause, CirclePlay, CircleX, History, Plus, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { type Subscriptions, subscriptionsApi } from "@/lib/everyday";
import { formatDate } from "@/lib/format";
import { formatMoney, parseAmount } from "@/lib/money";
import { type Occurrence, type Rule, recurringApi } from "@/lib/recurring";
import { cn } from "@/lib/utils";
import { AccountDialog, todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import { useAccountName, useFinanceError } from "../finance/parts";
import { MoneyInput } from "../money-input";
import { Button, Notice, Spinner } from "../ui";
import { ConfirmDialog, OccurrenceRow } from "./parts";
import s from "./subscriptions.module.css";
import { Select } from "../select";
import { ConfirmDialog as ConfirmAction } from "../confirm-dialog";
import { useLoad } from "../use-load";
import { DeleteOrArchive, useRemovalFailure } from "../removal";

/**
 * Subscriptions are recurring rules marked as such: the same engine schedules them, reminds
 * about them and asks the person to confirm each payment. Nothing is marked paid by itself, and a
 * payment changes a balance only once confirmed. Costs are per currency, never combined.
 */
export function SubscriptionsView() {
  return (
    <FinanceGate>
      <SubscriptionsPage />
    </FinanceGate>
  );
}

export function useSubscriptions() {
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const [attempt, setAttempt] = useState(0);
  const stamp = `${revision}:${attempt}`;
  const [state, setState] = useState<{ for: string; value?: Subscriptions; failed?: boolean }>({
    for: "",
  });
  useEffect(() => {
    let cancelled = false;
    subscriptionsApi
      .get()
      .then((value) => {
        if (!cancelled) setState({ for: stamp, value });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setState({ for: stamp, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [stamp, handleAuthError]);
  return { ...state, reload: () => setAttempt((n) => n + 1) };
}

/** Every payment this subscription expected: confirmed ones with what was paid, and the rest. */
function SubscriptionHistory({ rule, onClose }: { rule: Rule; onClose: () => void }) {
  const t = useTranslations("app.subscriptions.history");
  const locale = useLocale() as Locale;
  const { data, error } = useLoad(`subscription-history:${rule.id}`, () =>
    recurringApi.occurrences({ rule_id: rule.id }),
  );
  const rows = [...(data ?? [])].sort((a, b) => b.window_start.localeCompare(a.window_start));
  return (
    <AccountDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={t("title", { name: rule.name })}
      lead={t("lead")}
      footer={
        <Button variant="ghost" onClick={onClose}>
          {t("close")}
        </Button>
      }
    >
      {error ? (
        <Notice tone="error">{t("failed")}</Notice>
      ) : !data ? (
        <p className={s.note}>
          <Spinner />
        </p>
      ) : rows.length === 0 ? (
        <p className={s.note}>{t("empty")}</p>
      ) : (
        <ul className={s.historyList}>
          {rows.map((o) => (
            <li key={o.id}>
              <span>{formatDate(`${o.window_start}T12:00:00Z`, locale, "UTC")}</span>
              <span className={s.historyAmount}>
                {o.actual
                  ? formatMoney(o.actual, locale)
                  : o.expected
                    ? formatMoney(o.expected, locale)
                    : "—"}
              </span>
              <span className={s.status} data-status={o.state}>
                {t(`states.${o.state}`)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </AccountDialog>
  );
}

function SubscriptionsPage() {
  const t = useTranslations("app.subscriptions");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const accountName = useAccountName();
  const message = useFinanceError();
  const { value, failed, reload } = useSubscriptions();
  const [formOpen, setFormOpen] = useState(false);
  const [formKey, setFormKey] = useState(0);
  const [confirm, setConfirm] = useState<{ n: number; occurrence: Occurrence } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const [cancelling, setCancelling] = useState<Rule | null>(null);
  const [history, setHistory] = useState<Rule | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const removalFailure = useRemovalFailure();

  // Success is shown only once the API has accepted the change; a refusal says why.
  const status = async (rule: Rule, action: "pause" | "resume" | "end") => {
    setNotice(null);
    try {
      await recurringApi.setStatus(rule.id, rule.version, action);
      setNotice({ tone: "success", text: t(`done.${action}`) });
      reload();
      return null;
    } catch (error) {
      if (handleAuthError(error)) return null;
      const text = message(error);
      setNotice({ tone: "error", text });
      return text;
    }
  };

  const items = value?.items ?? [];
  const order = { active: 0, paused: 1, ended: 2 } as const;
  const sorted = [...items].sort((a, b) => order[a.status] - order[b.status]);

  return (
    <div className={s.page}>
      <div className={s.head}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <Button
          variant="primary"
          onClick={() => {
            setFormKey((n) => n + 1);
            setFormOpen(true);
          }}
        >
          <Plus aria-hidden="true" />
          {t("new")}
        </Button>
      </div>
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}
      {failed ? (
        <Notice tone="error">{message(null)}</Notice>
      ) : !value ? (
        <p className={appStyles.muted} role="status">
          <Spinner />
        </p>
      ) : items.length === 0 ? (
        <div className={s.empty}>
          <p className={s.emptyTitle}>{t("emptyTitle")}</p>
          <p className={appStyles.muted}>{t("emptyBody")}</p>
        </div>
      ) : (
        <>
          <section className={s.costs} aria-label={t("costs")}>
            {value.costs.map((c) => (
              <div key={c.currency} className={s.cost}>
                <span className={s.costLabel}>
                  <bdi dir="ltr">{c.currency}</bdi>
                </span>
                <span className={s.costFigure}>
                  <span className={s.costMain}>
                    {c.approximate ? "≈ " : ""}
                    <bdi dir="ltr">{formatMoney(c.monthly, locale)}</bdi>
                  </span>
                  <span className={s.costUnit}>{t("perMonth")}</span>
                </span>
                <span className={s.costFigure}>
                  <span className={s.costMain}>
                    <bdi dir="ltr">{formatMoney(c.annual, locale)}</bdi>
                  </span>
                  <span className={s.costUnit}>{t("perYear")}</span>
                </span>
                {c.variable ? (
                  <span className={s.costNote}>{t("variable", { count: c.variable })}</span>
                ) : null}
              </div>
            ))}
            <p className={s.note}>{t("costsNote")}</p>
          </section>

          {value.upcoming.length > 0 ? (
            <section className={s.card} aria-labelledby="sub-upcoming">
              <h2 id="sub-upcoming" className={s.cardTitle}>
                {t("upcoming")}
              </h2>
              <ul className={s.occurrences}>
                {value.upcoming.map((o) => (
                  <OccurrenceRow
                    key={o.id}
                    occurrence={o}
                    onConfirm={(occurrence) => {
                      setConfirm((p) => ({ n: (p?.n ?? 0) + 1, occurrence }));
                      setConfirmOpen(true);
                    }}
                    onAnswered={reload}
                  />
                ))}
              </ul>
              <p className={s.note}>{t("confirmNote")}</p>
            </section>
          ) : null}

          <section className={s.card} aria-labelledby="sub-all">
            <h2 id="sub-all" className={s.cardTitle}>
              {t("all")}
            </h2>
            <ul className={s.list}>
              {sorted.map((r) => (
                <li key={r.id} className={cn(s.item, r.status !== "active" && s.dim)}>
                  <span className={s.itemText}>
                    <span className={s.itemName} dir="auto">
                      {r.name}
                    </span>
                    <span className={s.itemMeta}>
                      <bdi>{accountName(r.account)}</bdi>
                      {r.next && r.status === "active"
                        ? ` · ${t("renews", {
                            date: formatDate(`${r.next.window_start}T12:00:00Z`, locale, "UTC"),
                          })}`
                        : ""}
                    </span>
                  </span>
                  <span className={s.itemAmount}>
                    {r.amount ? (
                      <bdi dir="ltr">{formatMoney(r.amount, locale)}</bdi>
                    ) : (
                      <span className={s.note}>{t("varies")}</span>
                    )}
                    <span className={s.costUnit}>
                      {t(`every.${r.frequency === "yearly" ? "year" : "month"}`)}
                    </span>
                  </span>
                  <span className={s.status} data-status={r.status}>
                    {t(`status.${r.status}`)}
                  </span>
                  <span className={s.itemActions}>
                    {r.status === "active" ? (
                      <button
                        type="button"
                        className={s.iconAction}
                        aria-label={t("pause", { name: r.name })}
                        onClick={() => void status(r, "pause")}
                      >
                        <CirclePause aria-hidden="true" />
                      </button>
                    ) : r.status === "paused" ? (
                      <button
                        type="button"
                        className={s.iconAction}
                        aria-label={t("resume", { name: r.name })}
                        onClick={() => void status(r, "resume")}
                      >
                        <CirclePlay aria-hidden="true" />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className={s.iconAction}
                      aria-label={t("historyOpen", { name: r.name })}
                      onClick={() => setHistory(r)}
                    >
                      <History aria-hidden="true" />
                    </button>
                    {r.status !== "ended" ? (
                      <button
                        type="button"
                        className={s.iconAction}
                        aria-label={t("cancel", { name: r.name })}
                        onClick={() => setCancelling(r)}
                      >
                        <CircleX aria-hidden="true" />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className={s.iconAction}
                      aria-label={t("deleteOpen", { name: r.name })}
                      onClick={() => setDeleting(r)}
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      <ConfirmAction
        open={cancelling !== null}
        onOpenChange={(open) => !open && setCancelling(null)}
        title={t("cancelTitle", { name: cancelling?.name ?? "" })}
        body={<p>{t("cancelBody")}</p>}
        confirm={t("cancelAction")}
        onConfirm={async () => (cancelling ? status(cancelling, "end") : null)}
      />
      <ConfirmAction
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t("deleteTitle", { name: deleting?.name ?? "" })}
        body={
          <>
            <p>{t("deleteBody")}</p>
            <DeleteOrArchive canArchive={false} />
          </>
        }
        confirm={t("deleteAction")}
        onConfirm={async () => {
          if (!deleting) return null;
          try {
            await recurringApi.deleteRule(deleting.id, deleting.version);
            setNotice({ tone: "success", text: t("deleted") });
            reload();
            return null;
          } catch (error) {
            return removalFailure(error);
          }
        }}
      />
      {history ? <SubscriptionHistory rule={history} onClose={() => setHistory(null)} /> : null}
      <SubscriptionForm
        key={formKey}
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onDone={() => {
          setFormOpen(false);
          setNotice({ tone: "success", text: t("created") });
          reload();
        }}
      />
      {confirm ? (
        <ConfirmDialog
          key={confirm.n}
          occurrence={confirm.occurrence}
          open={confirmOpen}
          onClose={() => setConfirmOpen(false)}
          onDone={() => {
            setConfirmOpen(false);
            reload();
          }}
        />
      ) : null}
    </div>
  );
}

function SubscriptionForm({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const t = useTranslations("app.subscriptions.form");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, categories, currencies, defaultAccountId } = useFinance();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const active = accounts.filter((a) => a.status === "active");
  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState(
    active.find((a) => a.id === defaultAccountId)?.id ?? active[0]?.id ?? "",
  );
  const [amount, setAmount] = useState("");
  const [frequency, setFrequency] = useState<"monthly" | "yearly">("monthly");
  const [renewal, setRenewal] = useState(() => todayIn(user.time_zone));
  const [remind, setRemind] = useState(3);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const account = active.find((a) => a.id === accountId);
  const currency = account?.currency ?? "EUR";
  const exponent = currencies.find((c) => c.code === currency)?.exponent ?? 2;
  const parsed = amount.trim() ? parseAmount(amount, locale, exponent) : null;
  const category =
    categories.find((c) => c.system_key === "subscriptions" && c.status === "active") ??
    categories.find((c) => c.kind === "expense" && c.status === "active");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailure(null);
    if (!name.trim() || !account || !category || (parsed && !parsed.ok) || !renewal) {
      setFailure(t("incomplete"));
      return;
    }
    const day = Number(renewal.slice(8, 10));
    const month = Number(renewal.slice(5, 7));
    setBusy(true);
    try {
      await recurringApi.createRule(
        {
          kind: "expense",
          name: name.trim(),
          amount: parsed?.ok ? { amount: parsed.amount, currency } : null,
          currency,
          account_id: account.id,
          category_id: category.id,
          frequency,
          window_start_day: day,
          window_end_day: day,
          month: frequency === "yearly" ? month : null,
          start_date: renewal < todayIn(user.time_zone) ? renewal : todayIn(user.time_zone),
          remind_days_before: remind,
          reminders: remind > 0,
          is_subscription: true,
        },
        key,
      );
      onDone();
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
            {t("save")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-name`}>
            {t("name")}
          </label>
          <input
            id={`${id}-name`}
            className={appStyles.input}
            value={name}
            maxLength={100}
            placeholder={t("namePlaceholder")}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className={s.segmented} role="group" aria-label={t("frequency")}>
          {(["monthly", "yearly"] as const).map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={frequency === f}
              onClick={() => setFrequency(f)}
            >
              {t(f)}
            </button>
          ))}
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-account`}>
            {t("account")}
          </label>
          <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
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
            value={amount}
            onValueChange={setAmount}
            aria-invalid={parsed && !parsed.ok ? true : undefined}
          />
          <span className={appStyles.hint}>{t("amountHint")}</span>
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-renewal`}>
            {t("renewal")}
          </label>
          <input
            id={`${id}-renewal`}
            type="date"
            className={appStyles.input}
            value={renewal}
            onChange={(e) => setRenewal(e.target.value)}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-remind`}>
            {t("remind")}
          </label>
          <Select
            id={`${id}-remind`}
            value={remind}
            onValueChange={(value) => setRemind(Number(value))}
          >
            {[0, 1, 3, 7].map((d) => (
              <option key={d} value={d}>
                {d === 0 ? t("remindNone") : t("remindDays", { count: d })}
              </option>
            ))}
          </Select>
        </div>
      </form>
    </AccountDialog>
  );
}
