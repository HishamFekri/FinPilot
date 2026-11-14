"use client";

import { ArrowLeft, CircleCheck, FileText, Link2, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { ApiError } from "@/lib/api";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatMoney, isNegative, isZero } from "@/lib/money";
import {
  type Activity,
  type Dashboard,
  type Expense,
  type Member,
  type Settlement,
  settlementsApi,
  type Trip,
  inviteLink,
  tripsApi,
} from "@/lib/trips";
import { cn } from "@/lib/utils";
import { BackLink } from "../back-link";
import { AccountDialog, Money } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate } from "../finance/finance-provider";
import fin from "../finance/finance.module.css";
import { useFinanceError } from "../finance/parts";
import { Button, Loading, Notice } from "../ui";
import { DisputeBox, DisputedTag } from "./disputes";
import { ExpenseDialog } from "./expense-dialog";
import { ApproveAndRecord, MyTripMoney } from "./my-money";
import { PaymentRow, PaymentsCard, SettleDialog } from "./settlements";
import {
  ExpenseStatusTag,
  Initials,
  isolate,
  TripCategoryIcon,
  useDateRange,
  useTripCategory,
} from "./parts";
import s from "./trips.module.css";
import { PlanCard, TripAnalyticsCard, TripBudgetsCard, TripPhaseTag } from "./planning";
import { ConfirmDialog } from "../confirm-dialog";
import { useConfirm } from "../confirm-dialog";

export function TripView({ id }: { id: string }) {
  return (
    <FinanceGate>
      <TripPage id={id} />
    </FinanceGate>
  );
}

type Data = {
  trip: Trip;
  dashboard: Dashboard;
  expenses: Expense[];
  members: Member[];
  activity: Activity[] | null;
  payments: Settlement[];
};

function TripPage({ id }: { id: string }) {
  const t = useTranslations("app.trips");
  const [confirmEl, ask] = useConfirm();
  const user = useUser();
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const range = useDateRange();
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{
    for: number;
    value?: Data;
    failed?: boolean;
    missing?: boolean;
  } | null>(null);
  const [form, setForm] = useState<{ n: number; expense: Expense | null } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [detail, setDetail] = useState<{ n: number; id: string } | null>(null);
  const [settle, setSettle] = useState<{
    n: number;
    prefill: { recipient: string; amount: string; corrects?: string | null } | null;
  } | null>(null);
  const [settleOpen, setSettleOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      tripsApi.get(id),
      tripsApi.dashboard(id),
      tripsApi.expenses(id),
      tripsApi.members(id),
      settlementsApi.list(id),
    ])
      .then(async ([trip, dashboard, expenses, members, payments]) => {
        const activity =
          trip.member_status === "active" ? await tripsApi.activity(id).catch(() => null) : null;
        if (!cancelled)
          setData({
            for: attempt,
            value: { trip, dashboard, expenses, members, activity, payments },
          });
      })
      .catch((error: unknown) => {
        if (cancelled || handleAuthError(error)) return;
        const missing = error instanceof ApiError && error.status === 404;
        setData({ for: attempt, failed: true, missing });
      });
    return () => {
      cancelled = true;
    };
  }, [id, attempt, handleAuthError]);

  const reload = () => setAttempt((n) => n + 1);
  const value = data?.value;
  const openForm = (expense: Expense | null) => {
    setForm((prev) => ({ n: (prev?.n ?? 0) + 1, expense }));
    setFormOpen(true);
  };
  const openSettle = (
    prefill: { recipient: string; amount: string; corrects?: string | null } | null,
  ) => {
    setSettle((prev) => ({ n: (prev?.n ?? 0) + 1, prefill }));
    setSettleOpen(true);
  };
  const changed = (text: string) => {
    setNotice(text);
    reload();
  };
  const openDetail = (expenseId: string) => {
    setDetail((prev) => ({ n: (prev?.n ?? 0) + 1, id: expenseId }));
    setDetailOpen(true);
  };

  if (data?.failed) {
    return (
      <div className={s.page}>
        <BackLink href="/app/trips" className={s.back}>
          <ArrowLeft aria-hidden="true" className={fin.flipRtl} /> {t("back")}
        </BackLink>
        <Notice tone="error">
          {data.missing ? t("notFound") : t("loadFailed")}{" "}
          {data.missing ? null : (
            <button type="button" className={appStyles.linkBtn} onClick={reload}>
              {t("retry")}
            </button>
          )}
        </Notice>
      </div>
    );
  }
  if (!value) {
    return <Loading label={t("loading")} />;
  }

  const { trip, dashboard, expenses, members, activity, payments } = value;
  const attention =
    dashboard.waiting_on_me.length +
    dashboard.settlements_waiting.length +
    dashboard.disputes_attention.length;
  const dates = range(trip.start_date, trip.end_date);
  const statusAction = async (
    action: "complete" | "reopen" | "cancel" | "reinstate" | "archive" | "unarchive",
    acknowledge = false,
  ) => {
    if (
      action === "archive" &&
      !acknowledge &&
      !(await ask({
        title: t("archiveTitle"),
        body: t("archiveBody"),
        confirm: t("archive"),
        tone: "primary",
      }))
    )
      return;
    setFailure(null);
    try {
      await tripsApi.status(trip.id, action, acknowledge);
      setNotice(t(`statusDone.${action}`));
      reload();
    } catch (error) {
      if (handleAuthError(error)) return;
      const code = error instanceof ApiError ? error.code : null;
      if (action === "archive" && code === "balances_outstanding") {
        if (await ask({ title: t("archiveOutstanding") })) void statusAction("archive", true);
        return;
      }
      setFailure(message(error));
    }
  };

  return (
    <div className={s.page}>
      {confirmEl}
      <div>
        <BackLink href="/app/trips" className={s.back}>
          <ArrowLeft aria-hidden="true" className={fin.flipRtl} /> {t("back")}
        </BackLink>
      </div>
      <div className={s.head}>
        <div>
          <h1 className={appStyles.pageTitle} dir="auto">
            {trip.name}
          </h1>
          <p className={s.meta}>
            {trip.destination ? <span dir="auto">{trip.destination}</span> : null}
            {dates ? <span>{dates}</span> : null}
            <span>
              {t("baseCurrency")} <bdi dir="ltr">{trip.base_currency}</bdi>
            </span>
            <span>{t(`roles.${trip.role}`)}</span>
            <TripPhaseTag phase={trip.phase} />
          </p>
        </div>
        <div className={s.headActions}>
          <Link href={`/app/trips/${trip.id}/report`} className={s.reportLink}>
            <FileText aria-hidden="true" />
            {t("report.open")}
          </Link>
          {trip.can.admin && trip.status === "active" ? (
            <Button size="sm" variant="ghost" onClick={() => statusAction("complete")}>
              {t("complete")}
            </Button>
          ) : null}
          {trip.can.admin && trip.status === "completed" ? (
            <Button size="sm" variant="ghost" onClick={() => statusAction("reopen")}>
              {t("reopen")}
            </Button>
          ) : null}
          {trip.can.admin && trip.status === "active" ? (
            <Button size="sm" variant="ghost" onClick={() => setCancelling(true)}>
              {t("cancelTrip")}
            </Button>
          ) : null}
          {trip.can.admin && trip.status === "cancelled" ? (
            <Button size="sm" variant="ghost" onClick={() => statusAction("reinstate")}>
              {t("reinstate")}
            </Button>
          ) : null}
          {trip.can.owner && (trip.status === "completed" || trip.status === "cancelled") ? (
            <Button size="sm" variant="ghost" onClick={() => statusAction("archive")}>
              {t("archive")}
            </Button>
          ) : null}
          {trip.can.owner && trip.status === "archived" ? (
            <Button size="sm" variant="ghost" onClick={() => statusAction("unarchive")}>
              {t("unarchive")}
            </Button>
          ) : null}
          {trip.can.add_expense ? (
            <Button variant="primary" onClick={() => openForm(null)}>
              <Plus aria-hidden="true" />
              {t("addExpense")}
            </Button>
          ) : null}
        </div>
      </div>
      {notice ? <Notice tone="success">{notice}</Notice> : null}
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      {trip.member_status !== "active" ? <Notice>{t("formerNotice")}</Notice> : null}
      {trip.status !== "active" ? <Notice>{t(`readOnly.${trip.status}`)}</Notice> : null}

      {attention > 0 ? (
        <section className={cn(s.card, s.waiting)} aria-labelledby="waiting-title">
          <div className={s.cardHead}>
            <h2 id="waiting-title" className={s.cardTitle}>
              {t("waiting.title", { count: attention })}
            </h2>
          </div>
          <div className={s.cardBody}>
            <ul className={s.waitingList}>
              {dashboard.waiting_on_me.map((w) => (
                <li key={w.version_id} className={s.waitingItem}>
                  <span>
                    {t.rich(`waiting.${w.kind}`, {
                      name: w.proposed_by.display_name,
                      what: w.description,
                      amount: () => <Money value={w.amount} />,
                      b: (chunks) => <strong dir="auto">{chunks}</strong>,
                    })}
                  </span>
                  <Button size="sm" variant="primary" onClick={() => openDetail(w.expense_id)}>
                    {t("waiting.review")}
                  </Button>
                </li>
              ))}
              {dashboard.disputes_attention.map((d) => (
                <li key={d.id} className={s.waitingItem}>
                  <span>
                    {t("waiting.dispute", {
                      name: isolate(d.raised_by.display_name),
                      what: isolate(
                        expenses.find((e) => e.id === d.expense_id)?.shown.description ?? "",
                      ),
                    })}
                  </span>
                  <Button size="sm" onClick={() => openDetail(d.expense_id)}>
                    {t("waiting.review")}
                  </Button>
                </li>
              ))}
            </ul>
            {dashboard.settlements_waiting.length > 0 ? (
              <ul className={s.payments} style={{ marginBlockStart: 10 }}>
                {dashboard.settlements_waiting.map((x) => (
                  <PaymentRow key={x.id} trip={trip} settlement={x} onChanged={changed} />
                ))}
              </ul>
            ) : null}
          </div>
        </section>
      ) : null}

      <div className={s.layout}>
        <div className={s.main}>
          <section className={s.card} aria-labelledby="expenses-title">
            <div className={s.cardHead}>
              <h2 id="expenses-title" className={s.cardTitle}>
                {t("expenses.title")}
              </h2>
              <span className={s.note}>{t("expenses.count", { count: expenses.length })}</span>
            </div>
            {expenses.length === 0 ? (
              <div className={s.cardBody}>
                <p className={s.note}>{t("expenses.empty")}</p>
              </div>
            ) : (
              <ul className={cn(s.rows, appStyles.small)} style={{ marginBlockStart: 8 }}>
                {expenses.map((e) => (
                  <li key={e.id}>
                    <ExpenseRow expense={e} me={user.id} onOpen={() => openDetail(e.id)} />
                  </li>
                ))}
              </ul>
            )}
          </section>
          <PaymentsCard
            trip={trip}
            settlements={payments}
            waitingIds={new Set(dashboard.settlements_waiting.map((x) => x.id))}
            onChanged={changed}
            onCorrect={(x) =>
              openSettle({
                recipient: x.payer.user_id === user.id ? x.recipient.user_id : x.payer.user_id,
                amount: x.amount.amount,
                corrects: x.id,
              })
            }
          />
          {trip.member_status === "active" ? (
            <PlanCard
              trip={trip}
              writable={trip.status === "active" || trip.status === "completed"}
            />
          ) : null}
        </div>
        <div className={s.side}>
          <TripBudgetsCard trip={trip} onTripChanged={reload} />
          <MyTripMoney trip={trip} revision={attempt} onChanged={changed} />
          <BalancesCard
            dashboard={dashboard}
            me={user.id}
            canSettle={trip.status !== "archived" && members.length > 1}
            onSettle={openSettle}
          />
          <SummaryCard dashboard={dashboard} />
          {trip.member_status === "active" ? <TripAnalyticsCard trip={trip} /> : null}
          <MembersCard trip={trip} members={members} me={user.id} onChanged={reload} />
        </div>
      </div>
      {activity ? <ActivityCard activity={activity} /> : null}
      <ConfirmDialog
        open={cancelling}
        onOpenChange={setCancelling}
        title={t("cancelTitle")}
        body={<p>{t("cancelBody")}</p>}
        confirm={t("cancelTrip")}
        onConfirm={async () => {
          try {
            await tripsApi.status(trip.id, "cancel");
            setNotice(t("statusDone.cancel"));
            reload();
            return null;
          } catch (error) {
            if (handleAuthError(error)) return null;
            return message(error);
          }
        }}
      />

      {settle ? (
        <SettleDialog
          key={`settle-${settle.n}`}
          trip={trip}
          members={members}
          prefill={settle.prefill}
          open={settleOpen}
          onClose={() => setSettleOpen(false)}
          onSaved={(saved) => {
            setSettleOpen(false);
            changed(
              saved.warnings.includes("exceeds_outstanding_balance")
                ? t("settleSaved.over", { name: isolate(saved.recipient.display_name) })
                : t("settleSaved.done", { name: isolate(saved.recipient.display_name) }),
            );
          }}
        />
      ) : null}
      {form ? (
        <ExpenseDialog
          key={`form-${form.n}`}
          trip={trip}
          members={members}
          expense={form.expense}
          open={formOpen}
          onClose={() => setFormOpen(false)}
          onSaved={(saved) => {
            setFormOpen(false);
            setNotice(
              saved.status === "pending_payer" || saved.has_pending_correction
                ? t("saved.pending", { name: isolate(saved.payer.display_name) })
                : t("saved.done"),
            );
            reload();
          }}
        />
      ) : null}
      {detail ? (
        <ExpenseDetail
          key={`detail-${detail.n}`}
          trip={trip}
          expenseId={detail.id}
          open={detailOpen}
          onClose={() => setDetailOpen(false)}
          onCorrect={(e) => {
            setDetailOpen(false);
            openForm(e);
          }}
          onChanged={(text) => {
            setNotice(text);
            reload();
          }}
        />
      ) : null}
    </div>
  );
}

function ExpenseRow({ expense, me, onOpen }: { expense: Expense; me: string; onOpen: () => void }) {
  const t = useTranslations("app.trips.expenses");
  const locale = useLocale() as Locale;
  const v = expense.shown;
  const mine = v.shares.find((x) => x.user_id === me);
  const converted = v.amount.currency !== v.base_amount.currency;
  return (
    <button type="button" className={s.expenseRow} data-status={expense.status} onClick={onOpen}>
      <TripCategoryIcon categoryKey={v.category_key} />
      <span className={s.rowText}>
        <span className={s.rowTitle}>
          <span dir="auto">{v.description}</span>
          <ExpenseStatusTag expense={expense} />
          <DisputedTag count={expense.disputes.open_count} />
        </span>
        <span className={s.rowSub}>
          {expense.payer.user_id === me
            ? t("paidByYou")
            : t("paidBy", { name: isolate(expense.payer.display_name) })}
          {" · "}
          {formatDate(`${v.expense_date}T12:00:00Z`, locale, "UTC")}
          {mine ? ` · ${t("yourShare", { amount: formatMoney(mine.base_share, locale) })}` : ""}
        </span>
      </span>
      <span className={s.rowAmount}>
        <strong>
          <bdi dir="ltr">{formatMoney(converted ? v.amount : v.base_amount, locale)}</bdi>
        </strong>
        {converted ? (
          <span>
            <bdi dir="ltr">{`≈ ${formatMoney(v.base_amount, locale)}`}</bdi>
          </span>
        ) : null}
      </span>
    </button>
  );
}

function BalancesCard({
  dashboard,
  me,
  canSettle,
  onSettle,
}: {
  dashboard: Dashboard;
  me: string;
  canSettle: boolean;
  onSettle: (prefill: { recipient: string; amount: string } | null) => void;
}) {
  const t = useTranslations("app.trips.balances");
  const locale = useLocale() as Locale;
  return (
    <section className={s.card} aria-labelledby="balances-title">
      <div className={s.cardHead}>
        <h2 id="balances-title" className={s.cardTitle}>
          {t("title")}
        </h2>
        <span className={s.headActions}>
          {dashboard.balanced ? (
            <span className={s.check}>
              <CircleCheck aria-hidden="true" />
              {t("balanced")}
            </span>
          ) : null}
          {canSettle ? (
            <Button size="sm" onClick={() => onSettle(null)}>
              {t("settleUp")}
            </Button>
          ) : null}
        </span>
      </div>
      <div className={s.cardBody}>
        <ul className={s.balances}>
          {dashboard.members.map((m) => {
            const negative = isNegative(m.net);
            const zero = isZero(m.net);
            const amount = formatMoney(
              { ...m.net, amount: m.net.amount.replace(/^-/, "") },
              locale,
            );
            return (
              <li key={m.user_id} className={s.balance}>
                <Initials name={m.display_name} />
                <span className={s.balanceName} dir="auto">
                  {m.user_id === me ? t("you", { name: m.display_name }) : m.display_name}
                  {m.status !== "active" ? ` · ${t(`status.${m.status}`)}` : ""}
                </span>
                <span className={s.net} data-sign={zero ? undefined : negative ? "neg" : "pos"}>
                  {zero ? t("settled") : negative ? t("owes", { amount }) : t("isOwed", { amount })}
                </span>
                <span className={s.balanceSub}>
                  {t("paidOwed", {
                    paid: formatMoney(m.paid, locale),
                    owed: formatMoney(m.owed, locale),
                  })}
                  {!isZero(m.sent)
                    ? ` · ${t("sentOnly", { amount: formatMoney(m.sent, locale) })}`
                    : ""}
                  {!isZero(m.received)
                    ? ` · ${t("receivedOnly", { amount: formatMoney(m.received, locale) })}`
                    : ""}{" "}
                </span>
              </li>
            );
          })}
        </ul>
        {dashboard.suggestions.length > 0 ? (
          <>
            <ul className={s.suggestions} aria-label={t("suggested")}>
              {dashboard.suggestions.map((x, i) => (
                <li
                  key={`${x.payer.user_id}-${x.recipient.user_id}-${i}`}
                  className={s.suggestionRow}
                >
                  <span>
                    {t("pays", {
                      from: isolate(x.payer.display_name),
                      to: isolate(x.recipient.display_name),
                    })}
                    {x.involves_disputed_expense ? ` · ${t("disputedNote")}` : ""}
                  </span>
                  <span className={s.headActions}>
                    <Money value={x.amount} />
                    {canSettle && x.payer.user_id === me ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          onSettle({ recipient: x.recipient.user_id, amount: x.amount.amount })
                        }
                      >
                        {t("recordThis")}
                      </Button>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
            <p className={s.note}>{t("suggestedNote")}</p>
          </>
        ) : null}
      </div>
    </section>
  );
}

function SummaryCard({ dashboard }: { dashboard: Dashboard }) {
  const t = useTranslations("app.trips.summary");
  const categoryName = useTripCategory();
  return (
    <section className={s.card} aria-labelledby="summary-title">
      <div className={s.cardHead}>
        <h2 id="summary-title" className={s.cardTitle}>
          {t("title")}
        </h2>
      </div>
      <div className={s.cardBody}>
        <dl className={s.figures}>
          <div>
            <dt>{t("total")}</dt>
            <dd>
              <Money value={dashboard.total} />
            </dd>
          </div>
          <div>
            <dt>{t("shared")}</dt>
            <dd>
              <Money value={dashboard.shared} />
            </dd>
          </div>
          <div>
            <dt>{t("personal")}</dt>
            <dd>
              <Money value={dashboard.personal} />
            </dd>
          </div>
        </dl>
        {dashboard.categories.length > 0 ? (
          <ul className={s.suggestions}>
            {dashboard.categories.slice(0, 5).map((c) => (
              <li key={c.key}>
                <span>{categoryName(c.key)}</span>
                <Money value={c.amount} />
              </li>
            ))}
          </ul>
        ) : null}
        {dashboard.pending_count > 0 ? (
          <p className={s.note} style={{ marginBlockStart: 8 }}>
            {t("pendingNote", { count: dashboard.pending_count })}
          </p>
        ) : null}
      </div>
    </section>
  );
}

function MembersCard({
  trip,
  members,
  me,
  onChanged,
}: {
  trip: Trip;
  members: Member[];
  me: string;
  onChanged: () => void;
}) {
  const t = useTranslations("app.trips.members");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const invite = async () => {
    setBusy(true);
    setFailure(null);
    try {
      const created = await tripsApi.invite(trip.id, null);
      setLink(inviteLink(window.location.origin, locale, created.token));
      setCopied(false);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };
  const act = async (fn: () => Promise<unknown>) => {
    setFailure(null);
    try {
      await fn();
      onChanged();
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    }
  };

  const [confirmEl, ask] = useConfirm();
  return (
    <section className={s.card} aria-labelledby="members-title">
      {confirmEl}
      <div className={s.cardHead}>
        <h2 id="members-title" className={s.cardTitle}>
          {t("title")}
        </h2>
        {trip.can.admin && trip.status === "active" ? (
          <Button size="sm" onClick={invite} busy={busy}>
            <Link2 aria-hidden="true" />
            {t("invite")}
          </Button>
        ) : null}
      </div>
      <div className={s.cardBody}>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        <ul className={s.memberList}>
          {members.map((m) => (
            <li key={m.user_id} className={s.memberItem}>
              <Initials name={m.display_name} />
              <span>
                <span className={s.balanceName} dir="auto">
                  {m.user_id === me ? t("you", { name: m.display_name }) : m.display_name}
                </span>
                <span className={s.balanceSub}>
                  {" · "}
                  {m.status === "active" ? t(`roles.${m.role}`) : t(`status.${m.status}`)}
                </span>
              </span>
              <span className={s.memberActions}>
                {trip.can.owner && m.status === "active" && m.role !== "owner" ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      act(() =>
                        tripsApi.setRole(
                          trip.id,
                          m.user_id,
                          m.role === "admin" ? "member" : "admin",
                        ),
                      )
                    }
                  >
                    {m.role === "admin" ? t("makeMember") : t("makeAdmin")}
                  </Button>
                ) : null}
                {trip.can.admin &&
                m.status === "active" &&
                m.role !== "owner" &&
                m.user_id !== me ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      if (await ask({ title: t("removeConfirm", { name: m.display_name }) }))
                        void act(() => tripsApi.remove(trip.id, m.user_id));
                    }}
                  >
                    {t("remove")}
                  </Button>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
        {link ? (
          <div className={s.linkBox}>
            <span>{t("linkReady")}</span>
            <code>{link}</code>
            <span className={s.headActions}>
              <Button
                size="sm"
                onClick={() => {
                  void navigator.clipboard?.writeText(link).then(() => setCopied(true));
                }}
              >
                {copied ? t("copied") : t("copy")}
              </Button>
            </span>
            <span className={s.note}>{t("linkNote")}</span>
          </div>
        ) : null}
        {trip.member_status === "active" && trip.role !== "owner" ? (
          <p className={s.note} style={{ marginBlockStart: 10 }}>
            <button
              type="button"
              className={appStyles.linkBtn}
              onClick={async () => {
                if (await ask({ title: t("leaveConfirm") }))
                  void act(() => tripsApi.leave(trip.id));
              }}
            >
              {t("leave")}
            </button>
          </p>
        ) : null}
      </div>
    </section>
  );
}

function ActivityCard({ activity }: { activity: Activity[] }) {
  const t = useTranslations("app.trips.activity");
  const locale = useLocale() as Locale;
  const user = useUser();
  return (
    <section className={s.card} aria-labelledby="activity-title">
      <div className={s.cardHead}>
        <h2 id="activity-title" className={s.cardTitle}>
          {t("title")}
        </h2>
      </div>
      <div className={s.cardBody}>
        <ul className={s.activity}>
          {activity.map((e) => (
            <li key={e.id}>
              <span>
                {t.has(`actions.${e.action}` as "actions.trip.created")
                  ? t(`actions.${e.action}` as "actions.trip.created", {
                      name: isolate(e.actor?.display_name ?? t("someone")),
                    })
                  : e.action}
              </span>
              <time dateTime={e.occurred_at}>
                {formatDateTime(e.occurred_at, locale, user.time_zone)}
              </time>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function ExpenseDetail({
  trip,
  expenseId,
  open,
  onClose,
  onCorrect,
  onChanged,
}: {
  trip: Trip;
  expenseId: string;
  open: boolean;
  onClose: () => void;
  onCorrect: (expense: Expense) => void;
  onChanged: (notice: string) => void;
}) {
  const t = useTranslations("app.trips.detail");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const categoryName = useTripCategory();
  const [expense, setExpense] = useState<Expense | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    tripsApi
      .expense(trip.id, expenseId)
      .then((value) => {
        if (!cancelled) setExpense(value);
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [trip.id, expenseId, handleAuthError]);

  const run = async (label: string, fn: () => Promise<Expense>, done: string) => {
    setBusy(label);
    setFailure(null);
    try {
      const updated = await fn();
      setExpense(updated);
      onChanged(done);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(null);
    }
  };

  const v = expense?.current_version ?? expense?.shown ?? null;
  const pending = expense?.pending_version ?? null;

  const [confirmEl, ask] = useConfirm();
  return (
    <>
      {confirmEl}
      <AccountDialog
        open={open}
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
        title={v ? v.description : t("loading")}
        lead={expense ? <ExpenseStatusTag expense={expense} /> : undefined}
        footer={
          <Button variant="ghost" onClick={onClose}>
            {t("close")}
          </Button>
        }
      >
        <div>
          {failed ? <Notice tone="error">{t("loadFailed")}</Notice> : null}
          {failure ? <Notice tone="error">{failure}</Notice> : null}
          {!expense || !v ? (
            failed ? null : (
              <Loading label={t("loading")} />
            )
          ) : (
            <>
              {expense.status === "pending_payer" ? (
                <p className={s.notice}>
                  {t("notCounting", { name: isolate(expense.payer.display_name) })}
                </p>
              ) : null}
              <VersionBlock version={v} payer={expense.payer.display_name} />
              <DisputeBox
                trip={trip}
                expense={expense}
                onExpense={setExpense}
                onChanged={(text) => {
                  onChanged(text);
                  void tripsApi.expense(trip.id, expense.id).then(setExpense);
                }}
              />
              <p className={s.note} style={{ marginBlockStart: 8 }}>
                {categoryName(v.category_key)}
              </p>

              {pending && pending.id !== v.id ? (
                <div className={s.proposal}>
                  <strong>
                    {pending.kind === "cancellation"
                      ? t("proposedCancel", { name: isolate(pending.proposed_by.display_name) })
                      : t("proposedChange", { name: isolate(pending.proposed_by.display_name) })}
                  </strong>
                  {pending.kind !== "cancellation" ? (
                    <VersionBlock version={pending} payer={expense.payer.display_name} compact />
                  ) : null}
                  <p className={s.note}>{t("stillCounting")}</p>
                </div>
              ) : null}

              {expense.can.approve && pending ? (
                <div className={s.actions}>
                  <Button
                    variant="primary"
                    busy={busy === "approve"}
                    onClick={() =>
                      run(
                        "approve",
                        () => tripsApi.decide(trip.id, pending.id, "approve", newIdempotencyKey()),
                        t("approvedNotice"),
                      )
                    }
                  >
                    {t("approve")}
                  </Button>
                  {rejecting ? null : (
                    <Button onClick={() => setRejecting(true)}>{t("reject")}</Button>
                  )}
                  {pending.kind === "initial" && expense.payer.user_id === user.id ? (
                    <ApproveAndRecord
                      trip={trip}
                      versionId={pending.id}
                      amount={pending.amount}
                      onDone={(updated) => {
                        setExpense(updated);
                        onChanged(t("approvedRecorded"));
                      }}
                    />
                  ) : null}
                </div>
              ) : null}
              {rejecting && pending ? (
                <div className={appStyles.field} style={{ marginBlockStart: 10 }}>
                  <label className={appStyles.label} htmlFor="reject-reason">
                    {t("reason")}
                  </label>
                  <input
                    id="reject-reason"
                    className={appStyles.input}
                    dir="auto"
                    maxLength={500}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <div className={s.actions}>
                    <Button
                      variant="danger"
                      busy={busy === "reject"}
                      onClick={() =>
                        run(
                          "reject",
                          () =>
                            tripsApi.decide(
                              trip.id,
                              pending.id,
                              "reject",
                              newIdempotencyKey(),
                              reason,
                            ),
                          t("rejectedNotice"),
                        )
                      }
                    >
                      {t("confirmReject")}
                    </Button>
                    <Button variant="ghost" onClick={() => setRejecting(false)}>
                      {t("keep")}
                    </Button>
                  </div>
                </div>
              ) : null}

              <div className={s.actions}>
                {expense.can.correct ? (
                  <Button size="sm" onClick={() => onCorrect(expense)}>
                    {t("correct")}
                  </Button>
                ) : null}
                {expense.can.cancel ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    busy={busy === "cancel"}
                    onClick={async () => {
                      if (!(await ask({ title: t("cancelConfirm") }))) return;
                      void run(
                        "cancel",
                        () => tripsApi.cancel(trip.id, expense.id, newIdempotencyKey()),
                        expense.payer.user_id === user.id
                          ? t("cancelledNotice")
                          : t("cancelProposed"),
                      );
                    }}
                  >
                    {t("cancelExpense")}
                  </Button>
                ) : null}
                {expense.can.withdraw && pending ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    busy={busy === "withdraw"}
                    onClick={() =>
                      run(
                        "withdraw",
                        () => tripsApi.decide(trip.id, pending.id, "withdraw", newIdempotencyKey()),
                        t("withdrawnNotice"),
                      )
                    }
                  >
                    {t("withdraw")}
                  </Button>
                ) : null}
              </div>

              <h3 className={s.subhead}>{t("history")}</h3>
              <ol className={s.history}>
                {(expense.history ?? []).map((h) => (
                  <li key={h.id}>
                    {t(`historyRow.${h.kind}`, {
                      n: h.version_no,
                      name: isolate(h.proposed_by.display_name),
                      date: isolate(formatDateTime(h.created_at, locale, user.time_zone)),
                    })}
                    {" · "}
                    {t(`states.${h.state}`)}
                    {h.decision_reason ? ` · “${h.decision_reason}”` : ""}
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      </AccountDialog>
    </>
  );
}

function VersionBlock({
  version,
  payer,
  compact,
}: {
  version: NonNullable<Expense["current_version"]>;
  payer: string;
  compact?: boolean;
}) {
  const t = useTranslations("app.trips.detail");
  const fxT = useTranslations("app.fx");
  const locale = useLocale() as Locale;
  const converted = version.amount.currency !== version.base_amount.currency;
  const date = formatDate(`${version.expense_date}T12:00:00Z`, locale, "UTC");
  return (
    <div>
      {compact ? null : (
        <p className={s.detailAmount}>
          <bdi dir="ltr">{formatMoney(version.amount, locale)}</bdi>
        </p>
      )}
      <dl className={s.facts}>
        {compact ? (
          <div>
            <dt>{t("amount")}</dt>
            <dd>
              <bdi dir="ltr">{formatMoney(version.amount, locale)}</bdi>
            </dd>
          </div>
        ) : null}
        {converted ? (
          <div>
            <dt>{t("inBase")}</dt>
            <dd>
              <bdi dir="ltr">{formatMoney(version.base_amount, locale)}</bdi>
            </dd>
          </div>
        ) : null}
        {converted && version.rate ? (
          <div>
            <dt>{t("rate")}</dt>
            <dd>
              <bdi dir="ltr">
                {fxT("rateLine", {
                  from: version.amount.currency,
                  rate: version.rate.rate,
                  to: version.base_amount.currency,
                })}
              </bdi>
              {" · "}
              {version.rate.is_manual
                ? t("manualRate")
                : version.rate.upstream === "ECB"
                  ? fxT("sourceEcb", {
                      date: formatDate(`${version.rate.rate_date}T12:00:00Z`, locale, "UTC"),
                    })
                  : fxT("sourceBlended", {
                      date: formatDate(`${version.rate.rate_date}T12:00:00Z`, locale, "UTC"),
                    })}
            </dd>
          </div>
        ) : null}
        <div>
          <dt>{t("paidBy")}</dt>
          <dd dir="auto">{payer}</dd>
        </div>
        <div>
          <dt>{t("date")}</dt>
          <dd>{date}</dd>
        </div>
        <div>
          <dt>{t("split")}</dt>
          <dd>{t(`methods.${version.split_method}`)}</dd>
        </div>
      </dl>
      {version.shares.length > 0 ? (
        <table className={s.table} style={{ marginBlockStart: 10 }}>
          <thead>
            <tr>
              <th scope="col">{t("person")}</th>
              <th scope="col" className={s.num}>
                {t("share")}
              </th>
              {converted ? (
                <th scope="col" className={s.num}>
                  {t("inBase")}
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {version.shares.map((x) => (
              <tr key={x.user_id}>
                <th scope="row" dir="auto">
                  {x.display_name}
                </th>
                <td className={s.num}>
                  <bdi dir="ltr">{formatMoney(x.share, locale)}</bdi>
                </td>
                {converted ? (
                  <td className={s.num}>
                    <bdi dir="ltr">{formatMoney(x.base_share, locale)}</bdi>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {version.notes ? (
        <p className={s.note} dir="auto" style={{ marginBlockStart: 8 }}>
          {version.notes}
        </p>
      ) : null}
    </div>
  );
}
