"use client";

import { ArrowLeft, CalendarRange, HandCoins, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import type { Locale } from "@/i18n/routing";
import { goalsApi } from "@/lib/everyday";
import { formatDate } from "@/lib/format";
import { type InstallmentItem, type InstallmentPlan, installmentsApi } from "@/lib/installments";
import { formatMoney } from "@/lib/money";
import { BackLink } from "../back-link";
import { Money } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { ConfirmDialog } from "../confirm-dialog";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import { Select } from "../select";
import { Button, Loading, Notice, Tag } from "../ui";
import { useLoad } from "../use-load";
import { useInstallmentError } from "./errors";
import { PlanProgress } from "./installments-view";
import { PayDialog, RescheduleDialog } from "./plan-dialogs";
import s from "./installments.module.css";

export function PlanView({ id }: { id: string }) {
  return (
    <FinanceGate>
      <PlanPage id={id} />
    </FinanceGate>
  );
}

function StateTag({ item }: { item: InstallmentItem }) {
  const t = useTranslations("app.installments.states");
  const tone = item.state === "paid" ? "positive" : item.state === "overdue" ? "accent" : "neutral";
  return <Tag tone={tone}>{t(item.state)}</Tag>;
}

function Schedule({ items, caption }: { items: InstallmentItem[]; caption: string }) {
  const t = useTranslations("app.installments.schedule");
  const locale = useLocale() as Locale;
  const anyInterest = items.some((i) => Number(i.interest.amount) > 0);
  const anyFee = items.some((i) => Number(i.fee.amount) > 0);
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <caption className={s.caption}>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{t("number")}</th>
            <th scope="col">{t("due")}</th>
            <th scope="col">{t("principal")}</th>
            {anyInterest ? <th scope="col">{t("interest")}</th> : null}
            {anyFee ? <th scope="col">{t("fee")}</th> : null}
            <th scope="col">{t("paid")}</th>
            <th scope="col">{t("state")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id} data-state={i.state}>
              <th scope="row">{i.sequence}</th>
              <td>{formatDate(`${i.due_date}T12:00:00Z`, locale, "UTC")}</td>
              <td>
                <Money value={i.principal} />
              </td>
              {anyInterest ? (
                <td>
                  <Money value={i.interest} />
                </td>
              ) : null}
              {anyFee ? (
                <td>
                  <Money value={i.fee} />
                </td>
              ) : null}
              <td>
                <Money value={i.covered} />
              </td>
              <td>
                <StateTag item={i} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GoalLink({ plan, onChanged }: { plan: InstallmentPlan; onChanged: () => void }) {
  const t = useTranslations("app.installments.goal");
  const { handleAuthError } = useAuth();
  const message = useInstallmentError();
  const { data: goals } = useLoad("goals-for-plan", () => goalsApi.list());
  const [failure, setFailure] = useState<string | null>(null);
  const choices = (goals ?? []).filter(
    (g) => g.status !== "archived" && g.target.currency === plan.currency,
  );
  return (
    <div className={appStyles.field}>
      <label className={appStyles.label} htmlFor="plan-goal">
        {t("label")}
      </label>
      <Select
        id="plan-goal"
        value={plan.goal_id ?? ""}
        onValueChange={async (value) => {
          setFailure(null);
          try {
            await installmentsApi.update(plan.id, plan.version, { goal_id: value || null });
            onChanged();
          } catch (error) {
            if (!handleAuthError(error)) setFailure(message(error));
          }
        }}
      >
        <option value="">{t("none")}</option>
        {choices.map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
          </option>
        ))}
      </Select>
      <span className={appStyles.hint}>{t("hint")}</span>
      {failure ? <Notice tone="error">{failure}</Notice> : null}
    </div>
  );
}

function PlanPage({ id }: { id: string }) {
  const t = useTranslations("app.installments");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useInstallmentError();
  const { changed } = useFinance();
  const { data, error, reload } = useLoad(`plan:${id}`, () => installmentsApi.get(id));
  const [plan, setPlan] = useState<InstallmentPlan | null>(null);
  const [dialog, setDialog] = useState<"pay" | "reschedule" | "remove" | "archive" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const current = plan && data && plan.version >= data.version ? plan : data;

  if (error)
    return (
      <Notice tone="error">
        {t("failedOne")}{" "}
        <button type="button" className={s.inlineButton} onClick={reload}>
          {t("retry")}
        </button>
      </Notice>
    );
  if (!current) return <Loading label={t("loading")} />;

  const p = current;
  const updated = (next: InstallmentPlan, text?: string) => {
    setPlan(next);
    if (text) setNotice(text);
  };

  return (
    <div className={appStyles.stack}>
      <BackLink href="/app/installments" className={s.back}>
        <ArrowLeft aria-hidden="true" className={s.flipRtl} />
        {t("back")}
      </BackLink>
      <header className={s.pageHead}>
        <div>
          <h1 className={appStyles.pageTitle} dir="auto">
            {p.name}
          </h1>
          <p className={appStyles.pageLead}>
            {[p.lender, t(`origins.${p.origin}`)].filter(Boolean).join(" · ")}
          </p>
        </div>
        {p.status === "active" && !p.settled ? (
          <div className={s.headActions}>
            <Button variant="primary" onClick={() => setDialog("pay")}>
              <HandCoins aria-hidden="true" />
              {t("recordPayment")}
            </Button>
            <Button onClick={() => setDialog("reschedule")}>
              <CalendarRange aria-hidden="true" />
              {t("rescheduleAction")}
            </Button>
          </div>
        ) : null}
      </header>
      {notice ? <Notice tone="success">{notice}</Notice> : null}
      {p.status === "archived" ? <Notice>{t("archivedNotice")}</Notice> : null}

      <section className={s.card} aria-label={t("summary")}>
        <dl className={s.figures}>
          <div>
            <dt>{t("remaining")}</dt>
            <dd>
              <Money value={p.remaining} />
            </dd>
          </div>
          <div>
            <dt>{t("paidSoFar")}</dt>
            <dd>
              <Money value={p.paid} />
            </dd>
          </div>
          <div>
            <dt>{t("progress")}</dt>
            <dd>{t("paidOf", { paid: p.paid_count, total: p.installment_count })}</dd>
          </div>
          <div>
            <dt>{t("completion")}</dt>
            <dd>
              {p.completion_date
                ? formatDate(`${p.completion_date}T12:00:00Z`, locale, "UTC")
                : "—"}
            </dd>
          </div>
        </dl>
        <PlanProgress plan={p} />
        <dl className={s.details}>
          {p.item_price ? (
            <div>
              <dt>{t("price")}</dt>
              <dd>
                <Money value={p.item_price} />
              </dd>
            </div>
          ) : null}
          {p.down_payment ? (
            <div>
              <dt>{t("downPayment")}</dt>
              <dd>
                <Money value={p.down_payment} />
              </dd>
            </div>
          ) : null}
          <div>
            <dt>{t("financed")}</dt>
            <dd>
              <Money value={p.principal} />
            </dd>
          </div>
          {p.interest_mode === "fixed" ? (
            <>
              <div>
                <dt>{t("interestPaid")}</dt>
                <dd>
                  <Money value={p.interest_paid} />
                </dd>
              </div>
              <div>
                <dt>{t("feesPaid")}</dt>
                <dd>
                  <Money value={p.fees_paid} />
                </dd>
              </div>
              <div>
                <dt>{t("interestLeft")}</dt>
                <dd>
                  <Money value={p.scheduled_interest_left} />
                </dd>
              </div>
            </>
          ) : (
            <div>
              <dt>{t("interest")}</dt>
              <dd>{t("interestFree")}</dd>
            </div>
          )}
          {Number(p.ahead.amount) > 0 ? (
            <div>
              <dt>{t("ahead")}</dt>
              <dd>
                <Money value={p.ahead} />
              </dd>
            </div>
          ) : null}
          {Number(p.behind.amount) > 0 ? (
            <div>
              <dt>{t("behind")}</dt>
              <dd className={s.behind}>
                <Money value={p.behind} />
              </dd>
            </div>
          ) : null}
        </dl>
        {p.paid_before_count > 0 ? (
          <p className={s.note}>{t("paidBeforeNote", { count: p.paid_before_count })}</p>
        ) : null}
        <p className={s.note}>{t(`spendingNote.${p.origin}`)}</p>
      </section>

      {p.overdue_count > 0 ? (
        <Notice tone="error">{t("overdueNotice", { count: p.overdue_count })}</Notice>
      ) : null}

      <section className={s.card} aria-labelledby="schedule-title">
        <h2 id="schedule-title" className={s.subTitle}>
          {t("schedule.title")}
        </h2>
        <Schedule items={p.items ?? []} caption={t("schedule.caption")} />
        <p className={s.note}>{t("schedule.explain")}</p>
      </section>

      <section className={s.card} aria-labelledby="payments-title">
        <h2 id="payments-title" className={s.subTitle}>
          {t("payments.title")}
        </h2>
        {p.payments?.length ? (
          <ul className={s.payments}>
            {p.payments.map((x) => (
              <li key={x.id} data-voided={x.voided || undefined}>
                <span className={s.payDate}>
                  {formatDate(`${x.paid_on}T12:00:00Z`, locale, "UTC")}
                </span>
                <span className={s.payParts}>
                  {x.principal ? (
                    <span>
                      {t("payments.principal", { amount: formatMoney(x.principal, locale) })}
                    </span>
                  ) : null}
                  {x.interest ? (
                    <span>
                      {t("payments.interest", { amount: formatMoney(x.interest, locale) })}
                    </span>
                  ) : null}
                  {x.fee ? (
                    <span>{t("payments.fee", { amount: formatMoney(x.fee, locale) })}</span>
                  ) : null}
                </span>
                {x.voided ? <Tag>{t("payments.voided")}</Tag> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className={s.note}>{t("payments.empty")}</p>
        )}
        <p className={s.note}>{t("payments.explain")}</p>
      </section>

      {p.superseded?.length ? (
        <details className={s.card}>
          <summary className={s.subTitle}>
            {t("superseded", { count: p.superseded.length })}
          </summary>
          <Schedule items={p.superseded} caption={t("supersededCaption")} />
        </details>
      ) : null}

      <section className={s.card} aria-labelledby="plan-settings">
        <h2 id="plan-settings" className={s.subTitle}>
          {t("manage")}
        </h2>
        {p.status === "active" ? (
          <GoalLink
            plan={p}
            onChanged={() => {
              void changed();
              reload();
            }}
          />
        ) : null}
        <div className={s.manageActions}>
          {p.status === "active" && p.settled ? (
            <Button onClick={() => setDialog("archive")}>{t("archive")}</Button>
          ) : null}
          {p.status === "archived" ? (
            <Button
              onClick={async () => {
                try {
                  updated(await installmentsApi.action(p.id, p.version, "restore"), t("restored"));
                  await changed();
                } catch (e) {
                  if (!handleAuthError(e)) setNotice(message(e));
                }
              }}
            >
              {t("restore")}
            </Button>
          ) : null}
          {p.status === "active" ? (
            <Button variant="ghost" onClick={() => setDialog("remove")}>
              <Trash2 aria-hidden="true" />
              {t("remove")}
            </Button>
          ) : null}
        </div>
      </section>

      <PayDialog
        plan={p}
        open={dialog === "pay"}
        onOpenChange={(open) => setDialog(open ? "pay" : null)}
        onPaid={(next) => updated(next, t("paidNotice"))}
      />
      <RescheduleDialog
        plan={p}
        open={dialog === "reschedule"}
        onOpenChange={(open) => setDialog(open ? "reschedule" : null)}
        onDone={(next) => updated(next, t("rescheduledNotice"))}
      />
      <ConfirmDialog
        open={dialog === "remove"}
        onOpenChange={(open) => setDialog(open ? "remove" : null)}
        title={t("removeTitle")}
        body={<p>{t("removeBody")}</p>}
        confirm={t("remove")}
        onConfirm={async () => {
          try {
            updated(await installmentsApi.action(p.id, p.version, "remove"), t("removed"));
            await changed();
            return null;
          } catch (e) {
            if (handleAuthError(e)) return null;
            return message(e);
          }
        }}
      />
      <ConfirmDialog
        open={dialog === "archive"}
        onOpenChange={(open) => setDialog(open ? "archive" : null)}
        title={t("archiveTitle")}
        body={<p>{t("archiveBody")}</p>}
        confirm={t("archive")}
        tone="primary"
        onConfirm={async () => {
          try {
            updated(await installmentsApi.action(p.id, p.version, "archive"), t("archived"));
            await changed();
            return null;
          } catch (e) {
            if (handleAuthError(e)) return null;
            return message(e);
          }
        }}
      />
    </div>
  );
}
