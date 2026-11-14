"use client";

import { Minus, Pause, Play, Plus, Archive, ArchiveRestore, Target } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { Link } from "@/i18n/navigation";
import { newIdempotencyKey } from "@/lib/accounts";
import { type Goal, goalsApi } from "@/lib/everyday";
import { formatDate, formatDateTime, localeTag } from "@/lib/format";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import { AccountDialog } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import { useFinanceError } from "../finance/parts";
import { MoneyInput } from "../money-input";
import { Button, Loading, Notice } from "../ui";
import s from "./goals.module.css";
import { Select } from "../select";
import { useConfirm } from "../confirm-dialog";

/**
 * Savings goals. Progress is a number the person tracks by hand: it isn't money in an account,
 * it's never added to a balance, and nothing moves between accounts. The page says so.
 */
export function GoalsView() {
  return (
    <FinanceGate>
      <GoalsPage />
    </FinanceGate>
  );
}

export function useGoals(includeArchived = false) {
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const [attempt, setAttempt] = useState(0);
  const stamp = `${revision}:${attempt}:${includeArchived}`;
  const [state, setState] = useState<{ for: string; list?: Goal[]; failed?: boolean }>({ for: "" });
  useEffect(() => {
    let cancelled = false;
    goalsApi
      .list(includeArchived)
      .then((list) => {
        if (!cancelled) setState({ for: stamp, list });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setState({ for: stamp, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [stamp, includeArchived, handleAuthError]);
  return { ...state, reload: () => setAttempt((n) => n + 1) };
}

/** A progress bar that never exceeds its track; the text carries the exact figure. */
export function GoalBar({ goal }: { goal: Goal }) {
  const width = Math.min(Number(goal.percent), 100);
  return (
    <span className={s.track} aria-hidden="true">
      <span
        className={s.fill}
        data-done={goal.reached || undefined}
        style={{ inlineSize: `${width}%` }}
      />
    </span>
  );
}

function GoalsPage() {
  const t = useTranslations("app.goals");
  const locale = useLocale() as Locale;
  const [showArchived, setShowArchived] = useState(false);
  const { list, failed, reload } = useGoals(showArchived);
  const [dialog, setDialog] = useState<
    | { n: number; kind: "new" }
    | { n: number; kind: "move"; goal: Goal; direction: "add" | "reduce" }
    | { n: number; kind: "edit"; goal: Goal }
    | { n: number; kind: "history"; goal: Goal }
    | null
  >(null);
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const message = useFinanceError();
  const show = (next: NonNullable<typeof dialog>) => {
    setDialog(next);
    setOpen(true);
  };
  const done = (text: string) => {
    setOpen(false);
    setNotice(text);
    reload();
  };

  return (
    <div className={s.page}>
      <div className={s.head}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <Button variant="primary" onClick={() => show({ n: Date.now(), kind: "new" })}>
          <Plus aria-hidden="true" />
          {t("new")}
        </Button>
      </div>
      <Notice tone="info">{t("notMoney")}</Notice>
      {notice ? <Notice tone="success">{notice}</Notice> : null}
      <label className={s.check}>
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        />
        <span>{t("showArchived")}</span>
      </label>
      {failed ? (
        <Notice tone="error">{message(null)}</Notice>
      ) : !list ? (
        <Loading />
      ) : list.length === 0 ? (
        <div className={s.empty}>
          <Target aria-hidden="true" />
          <p className={s.emptyTitle}>{t("emptyTitle")}</p>
          <p className={appStyles.muted}>{t("emptyBody")}</p>
        </div>
      ) : (
        <ul className={s.list}>
          {list.map((g) => (
            <li key={g.id} className={cn(s.goal, g.status !== "active" && s.dim)}>
              <div className={s.goalTop}>
                <button
                  type="button"
                  className={s.goalName}
                  onClick={() => show({ n: Date.now(), kind: "history", goal: g })}
                >
                  <span dir="auto">{g.name}</span>
                </button>
                {g.status !== "active" ? (
                  <span className={s.tag}>{t(`status.${g.status}`)}</span>
                ) : g.reached ? (
                  <span className={s.tag} data-tone="positive">
                    {t("reached")}
                  </span>
                ) : null}
              </div>
              <p className={s.figures}>
                {t.rich("progressLine", {
                  progress: formatMoney(g.progress, locale),
                  target: formatMoney(g.target, locale),
                  b: (chunks) => <strong>{chunks}</strong>,
                })}
                <span className={s.percent}>
                  <bdi dir="ltr">{`${Number(g.percent).toLocaleString(localeTag(locale))}%`}</bdi>
                </span>
              </p>
              <GoalBar goal={g} />
              {g.plan_id ? (
                <p className={s.meta}>
                  {t.rich("followsPlan", {
                    name: g.plan_name ?? "",
                    link: (chunks) => (
                      <Link href={`/app/installments/${g.plan_id}`} className={s.planLink}>
                        {chunks}
                      </Link>
                    ),
                  })}
                </p>
              ) : null}
              <p className={s.meta}>
                {g.reached
                  ? t("doneMeta")
                  : t("remainingMeta", { amount: formatMoney(g.remaining, locale) })}
                {g.target_date
                  ? ` · ${t("byDate", {
                      date: formatDate(`${g.target_date}T12:00:00Z`, locale, "UTC"),
                    })}`
                  : ""}
              </p>
              <div className={s.actions}>
                {g.status === "active" && !g.plan_id ? (
                  <>
                    <Button
                      size="sm"
                      onClick={() =>
                        show({ n: Date.now(), kind: "move", goal: g, direction: "add" })
                      }
                    >
                      <Plus aria-hidden="true" />
                      {t("add")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        show({ n: Date.now(), kind: "move", goal: g, direction: "reduce" })
                      }
                    >
                      <Minus aria-hidden="true" />
                      {t("reduce")}
                    </Button>
                  </>
                ) : null}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => show({ n: Date.now(), kind: "edit", goal: g })}
                >
                  {t("edit")}
                </Button>
                <StatusButton goal={g} onDone={done} />
              </div>
            </li>
          ))}
        </ul>
      )}

      {dialog?.kind === "new" ? (
        <GoalForm
          key={dialog.n}
          goal={null}
          open={open}
          onClose={() => setOpen(false)}
          onDone={done}
        />
      ) : dialog?.kind === "edit" ? (
        <GoalForm
          key={dialog.n}
          goal={dialog.goal}
          open={open}
          onClose={() => setOpen(false)}
          onDone={done}
        />
      ) : dialog?.kind === "move" ? (
        <MoveForm
          key={dialog.n}
          goal={dialog.goal}
          direction={dialog.direction}
          open={open}
          onClose={() => setOpen(false)}
          onDone={done}
        />
      ) : dialog?.kind === "history" ? (
        <HistoryDialog
          key={dialog.n}
          goal={dialog.goal}
          open={open}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </div>
  );
}

function StatusButton({ goal, onDone }: { goal: Goal; onDone: (text: string) => void }) {
  const t = useTranslations("app.goals");
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [failure, setFailure] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();
  const change = async (status: Goal["status"]) => {
    if (
      status === "archived" &&
      !(await ask({
        title: t("archiveTitle", { name: goal.name }),
        body: t("archiveBody"),
        confirm: t("archive"),
        tone: "primary",
      }))
    )
      return;
    setFailure(null);
    try {
      await goalsApi.update(goal.id, goal.version, { status });
      onDone(t(`statusDone.${status}`));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    }
  };
  return (
    <>
      {goal.status === "active" ? (
        <Button size="sm" variant="ghost" onClick={() => void change("paused")}>
          <Pause aria-hidden="true" />
          {t("pause")}
        </Button>
      ) : goal.status === "paused" ? (
        <Button size="sm" variant="ghost" onClick={() => void change("active")}>
          <Play aria-hidden="true" />
          {t("resume")}
        </Button>
      ) : null}
      {goal.status !== "archived" ? (
        <Button size="sm" variant="ghost" onClick={() => void change("archived")}>
          <Archive aria-hidden="true" />
          {t("archive")}
        </Button>
      ) : (
        <Button size="sm" variant="ghost" onClick={() => void change("active")}>
          <ArchiveRestore aria-hidden="true" />
          {t("restore")}
        </Button>
      )}
      {failure ? <span className={appStyles.error}>{failure}</span> : null}
      {confirmEl}
    </>
  );
}

function GoalForm({
  goal,
  open,
  onClose,
  onDone,
}: {
  goal: Goal | null;
  open: boolean;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const t = useTranslations("app.goals");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const { accounts, currencies, defaultAccountId } = useFinance();
  const message = useFinanceError();
  const id = useId();
  const home = accounts.find((a) => a.id === defaultAccountId)?.currency ?? accounts[0]?.currency;
  const [name, setName] = useState(goal?.name ?? "");
  const [currency, setCurrency] = useState(goal?.target.currency ?? home ?? "EUR");
  const [target, setTarget] = useState(goal ? editableAmount(goal.target.amount, locale) : "");
  const [starting, setStarting] = useState("");
  const [date, setDate] = useState(goal?.target_date ?? "");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const exponent = currencies.find((c) => c.code === currency)?.exponent ?? 2;
  const parsedTarget = target.trim() ? parseAmount(target, locale, exponent) : null;
  const parsedStart = starting.trim() ? parseAmount(starting, locale, exponent) : null;
  const codes = [...new Set([currency, ...accounts.map((a) => a.currency), "EUR", "USD", "TRY"])];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailure(null);
    if (!name.trim() || !parsedTarget?.ok || (parsedStart && !parsedStart.ok)) {
      setFailure(t("incomplete"));
      return;
    }
    setBusy(true);
    try {
      if (goal) {
        await goalsApi.update(goal.id, goal.version, {
          name: name.trim(),
          target: parsedTarget.amount,
          ...(date ? { target_date: date } : { clear_target_date: true }),
        });
        onDone(t("saved"));
      } else {
        await goalsApi.create(
          {
            name: name.trim(),
            target: { amount: parsedTarget.amount, currency },
            target_date: date || null,
            starting: parsedStart?.ok ? parsedStart.amount : null,
          },
          key,
        );
        onDone(t("created"));
      }
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
      title={goal ? t("editTitle") : t("newTitle")}
      lead={t("formLead")}
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
            maxLength={60}
            placeholder={t("namePlaceholder")}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className={s.row}>
          {!goal ? (
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-currency`}>
                {t("currency")}
              </label>
              <Select id={`${id}-currency`} value={currency} dir="ltr" onValueChange={setCurrency}>
                {codes.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </div>
          ) : null}
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-target`}>
              {t("target", { currency })}
            </label>
            <MoneyInput
              id={`${id}-target`}
              className={appStyles.input}
              value={target}
              onValueChange={setTarget}
              aria-invalid={parsedTarget && !parsedTarget.ok ? true : undefined}
            />
          </div>
        </div>
        {!goal ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-start`}>
              {t("starting")}
            </label>
            <MoneyInput
              id={`${id}-start`}
              className={appStyles.input}
              value={starting}
              onValueChange={setStarting}
            />
            <span className={appStyles.hint}>{t("startingHint")}</span>
          </div>
        ) : null}
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
      </form>
    </AccountDialog>
  );
}

function MoveForm({
  goal,
  direction,
  open,
  onClose,
  onDone,
}: {
  goal: Goal;
  direction: "add" | "reduce";
  open: boolean;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const t = useTranslations("app.goals");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const { currencies } = useFinance();
  const message = useFinanceError();
  const id = useId();
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const code = goal.target.currency;
  const parsed = amount.trim()
    ? parseAmount(amount, locale, currencies.find((c) => c.code === code)?.exponent ?? 2)
    : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailure(null);
    if (!parsed?.ok) {
      setFailure(t("incomplete"));
      return;
    }
    setBusy(true);
    try {
      await goalsApi.move(goal.id, direction, parsed.amount, note.trim() || null, key);
      onDone(direction === "add" ? t("added") : t("reduced"));
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
        direction === "add"
          ? t("addTitle", { name: goal.name })
          : t("reduceTitle", { name: goal.name })
      }
      lead={t("moveLead")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {direction === "add" ? t("add") : t("reduce")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        <p className={appStyles.muted}>
          {t("nowLine", {
            progress: formatMoney(goal.progress, locale),
            target: formatMoney(goal.target, locale),
          })}
        </p>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {t("amount", { currency: code })}
          </label>
          <MoneyInput
            id={`${id}-amount`}
            className={appStyles.input}
            value={amount}
            onValueChange={setAmount}
            aria-invalid={parsed && !parsed.ok ? true : undefined}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-note`}>
            {t("note")}
          </label>
          <input
            id={`${id}-note`}
            className={appStyles.input}
            value={note}
            maxLength={200}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
      </form>
    </AccountDialog>
  );
}

function HistoryDialog({
  goal,
  open,
  onClose,
}: {
  goal: Goal;
  open: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("app.goals");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const [full, setFull] = useState<Goal | null>(null);
  useEffect(() => {
    let cancelled = false;
    goalsApi
      .get(goal.id)
      .then((g) => {
        if (!cancelled) setFull(g);
      })
      .catch((error: unknown) => {
        handleAuthError(error);
      });
    return () => {
      cancelled = true;
    };
  }, [goal.id, handleAuthError]);
  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("historyTitle", { name: goal.name })}
      lead={t("historyLead")}
      footer={
        <Button variant="primary" onClick={onClose}>
          {t("close")}
        </Button>
      }
    >
      {!full ? (
        <Loading />
      ) : (
        <ol className={s.history}>
          {(full.history ?? []).map((e) => (
            <li key={e.id}>
              <span className={s.historyWhat}>
                {t(`event.${e.kind}`, {
                  amount: e.amount ? formatMoney(e.amount, locale) : "",
                  target: e.target ? formatMoney(e.target, locale) : "",
                  status: e.status ? t(`status.${e.status as Goal["status"]}`) : "",
                })}
                {e.note ? <span className={s.historyNote} dir="auto">{` · ${e.note}`}</span> : null}
              </span>
              <bdi className={s.historyWhen}>{formatDateTime(e.created_at, locale)}</bdi>
            </li>
          ))}
        </ol>
      )}
    </AccountDialog>
  );
}
