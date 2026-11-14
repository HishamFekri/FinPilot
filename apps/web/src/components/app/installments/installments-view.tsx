"use client";

import { CalendarClock, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import type { Locale } from "@/i18n/routing";
import { Link, useRouter } from "@/i18n/navigation";
import { formatDate } from "@/lib/format";
import { type InstallmentPlan, installmentsApi } from "@/lib/installments";
import { Money } from "../accounts/parts";
import appStyles from "../app.module.css";
import { FinanceGate } from "../finance/finance-provider";
import { Button, Loading, Notice, Tag } from "../ui";
import { useLoad } from "../use-load";
import { InstallmentAnalytics } from "./installment-analytics";
import { PlanDialog } from "./plan-form";
import s from "./installments.module.css";

export function InstallmentsView() {
  return (
    <FinanceGate>
      <InstallmentsPage />
    </FinanceGate>
  );
}

/** Paid / total, as a bar that never overflows; the text beside it carries the figures. */
export function PlanProgress({ plan }: { plan: InstallmentPlan }) {
  const percent = plan.installment_count
    ? Math.min(100, Math.round((plan.paid_count / plan.installment_count) * 100))
    : 0;
  return (
    <span className={s.track} aria-hidden="true">
      <span
        className={s.fill}
        data-done={plan.settled || undefined}
        style={{ inlineSize: `${percent}%` }}
      />
    </span>
  );
}

function PlanCard({ plan }: { plan: InstallmentPlan }) {
  const t = useTranslations("app.installments");
  const locale = useLocale() as Locale;
  const next = plan.next_due;
  return (
    <li>
      <Link href={`/app/installments/${plan.id}`} className={s.planCard}>
        <span className={s.planTop}>
          <span className={s.planName} dir="auto">
            {plan.name}
          </span>
          {plan.overdue_count > 0 ? (
            <Tag tone="accent">{t("overdueCount", { count: plan.overdue_count })}</Tag>
          ) : plan.settled ? (
            <Tag tone="positive">{t("settled")}</Tag>
          ) : null}
        </span>
        {plan.lender ? (
          <span className={s.planSub} dir="auto">
            {plan.lender}
          </span>
        ) : null}
        <span className={s.planFigures}>
          <span>
            <span className={s.figLabel}>{t("remaining")}</span>
            <strong>
              <Money value={plan.remaining} />
            </strong>
          </span>
          <span>
            <span className={s.figLabel}>{t("progress")}</span>
            <strong>{t("paidOf", { paid: plan.paid_count, total: plan.installment_count })}</strong>
          </span>
        </span>
        <PlanProgress plan={plan} />
        {next ? (
          <span className={s.planNext}>
            <CalendarClock aria-hidden="true" />
            {t("nextDue", {
              date: formatDate(`${next.due_date}T12:00:00Z`, locale, "UTC"),
              amount: "",
            })}
            <Money value={next.total} />
          </span>
        ) : null}
      </Link>
    </li>
  );
}

function InstallmentsPage() {
  const t = useTranslations("app.installments");
  const router = useRouter();
  const [showArchived, setShowArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const { data, error, reload } = useLoad(`installments:${showArchived}`, () =>
    installmentsApi.list(showArchived),
  );

  return (
    <div className={appStyles.stack}>
      <header className={s.pageHead}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus aria-hidden="true" />
          {t("new")}
        </Button>
      </header>

      {error ? (
        <Notice tone="error">
          {t("failed")}{" "}
          <button type="button" className={s.inlineButton} onClick={reload}>
            {t("retry")}
          </button>
        </Notice>
      ) : !data ? (
        <Loading label={t("loading")} />
      ) : data.length === 0 ? (
        <div className={s.emptyState}>
          <h2 className={s.subTitle}>{t("emptyTitle")}</h2>
          <p className={s.note}>{t("emptyBody")}</p>
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus aria-hidden="true" />
            {t("new")}
          </Button>
        </div>
      ) : (
        <ul className={s.planList}>
          {data.map((plan) => (
            <PlanCard key={plan.id} plan={plan} />
          ))}
        </ul>
      )}

      <label className={s.toggle}>
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        />
        {t("showArchived")}
      </label>

      <section className={appStyles.stack} aria-labelledby="installment-analytics">
        <h2 id="installment-analytics" className={s.sectionTitle}>
          {t("analytics.title")}
        </h2>
        <InstallmentAnalytics showLinks={false} />
      </section>

      <p className={s.note}>{t("rules")}</p>

      <PlanDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(id) => router.push(`/app/installments/${id}`)}
      />
    </div>
  );
}
