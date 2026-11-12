"use client";

import {
  CalendarDays,
  ChartColumn,
  CreditCard,
  Target,
  ChevronRight,
  FileText,
  PiggyBank,
  Repeat,
  Scale,
  Tags,
  Wallet,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { financeApi } from "@/lib/finance";
import { localeTag } from "@/lib/format";
import { isNegative, isZero } from "@/lib/money";
import { todayIn } from "./accounts/parts";
import { useUser } from "./auth-provider";
import { EquivalentsDialog } from "./finance/equivalents";
import { FinanceGate, useFinance } from "./finance/finance-provider";
import s from "./finance/finance.module.css";
import { AddButton } from "./finance/quick-add-dialog";
import { TemplateStrip } from "./finance/templates";
import { TransactionDialog, useTxnDialog } from "./finance/transaction-dialog";
import { HomeActivity } from "./home/activity";
import { HomeCategories } from "./home/categories";
import { Dashboard } from "./home/dashboard";
import h from "./home/home.module.css";
import { HomeBudgets, HomeGoals } from "./home/plans";
import { useRemote } from "./home/remote";
import { HomeSummary } from "./home/summary";
import { HomeTimeline } from "./home/timeline";
import { bdi } from "./ui";

/**
 * Home: where the money stands, what just happened, where it went, and what's next. Every figure
 * comes from the ledger; nothing is sample data. Expected payments are never counted as paid, and
 * amounts in different currencies are only combined where it's marked as approximate.
 */
export function HomeView() {
  return (
    <FinanceGate>
      <Home />
    </FinanceGate>
  );
}

function Home() {
  const t = useTranslations("app.home");
  const d = useTranslations("app.dashboard.widgets");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { revision } = useFinance();
  const dialog = useTxnDialog();
  const firstName = user.display_name?.trim().split(/\s+/u)[0];

  const month = todayIn(user.time_zone).slice(0, 7);
  const monthName = new Intl.DateTimeFormat(localeTag(locale), {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${month}-15T12:00:00Z`));

  const [summary, retrySummary] = useRemote(`${month}:${revision}`, () =>
    financeApi.summary(month),
  );
  const [recent, retryRecent] = useRemote(String(revision), () => financeApi.history({}, null, 6));

  return (
    <>
      <Dashboard
        heading={
          <>
            <h1 className={h.title}>{t("focus")}</h1>
            <p className={h.lead}>
              {firstName
                ? t.rich("leadNamed", { name: firstName, month: monthName, bdi })
                : t.rich("monthLead", { month: monthName, bdi })}
            </p>
          </>
        }
        action={<AddButton variant="header" />}
        widgets={{
          summary: {
            title: d("summary"),
            node: <HomeSummary month={month} summary={summary} />,
          },
          recent: {
            title: d("recent"),
            node: (
              <HomeActivity
                state={recent}
                onRetry={retryRecent}
                onOpen={(item) => dialog.show(item.id)}
              />
            ),
          },
          categories: {
            title: d("categories"),
            node: <HomeCategories state={summary} onRetry={retrySummary} />,
          },
          upcoming: { title: d("upcoming"), node: <HomeTimeline /> },
          budgets: { title: d("budgets"), node: <HomeBudgets month={month} /> },
          goals: { title: d("goals"), node: <HomeGoals /> },
          templates: { title: d("templates"), node: <TemplateStrip /> },
          links: { title: d("links"), node: <LinksPanel /> },
        }}
      />

      <TransactionDialog dialog={dialog} />
    </>
  );
}

function LinksPanel() {
  const t = useTranslations("app.home.links");
  const { accounts, defaultAccountId } = useFinance();
  const [equivalentOpen, setEquivalentOpen] = useState(false);
  // Starts from a positive balance (the default account's if it has one); any amount can be typed.
  const positive = (a: (typeof accounts)[number]) => !isZero(a.balance) && !isNegative(a.balance);
  const home =
    accounts.find((a) => a.id === defaultAccountId && positive(a)) ??
    accounts.find(positive) ??
    accounts[0];
  const initial =
    home && positive(home) ? home.balance : { amount: "100", currency: home?.currency ?? "USD" };
  const links = [
    { href: "/app/calendar", icon: CalendarDays, label: t("calendar") },
    { href: "/app/accounts", icon: Wallet, label: t("accounts") },
    { href: "/app/recurring", icon: Repeat, label: t("recurring") },
    { href: "/app/subscriptions", icon: CreditCard, label: t("subscriptions") },
    { href: "/app/goals", icon: Target, label: t("goals") },
    { href: "/app/settings/categories", icon: Tags, label: t("categories") },
    { href: "/app/budgets", icon: PiggyBank, label: t("budgets") },
    { href: "/app/analytics", icon: ChartColumn, label: t("insights") },
    { href: "/app/analytics?tab=reports", icon: FileText, label: t("reports") },
  ];
  return (
    <nav className={h.section} aria-labelledby="links-title">
      <div className={h.sectionHead}>
        <h2 id="links-title" className={h.sectionTitle}>
          {t("title")}
        </h2>
      </div>
      <ul className={s.links}>
        {links.map(({ href, icon: Icon, label }) => (
          <li key={href}>
            <Link href={href} className={s.linkRow}>
              <Icon aria-hidden="true" />
              <span>{label}</span>
              <ChevronRight aria-hidden="true" className={s.flipRtl} />
            </Link>
          </li>
        ))}
        <li>
          <button type="button" className={s.linkRow} onClick={() => setEquivalentOpen(true)}>
            <Scale aria-hidden="true" />
            <span>{t("equivalent")}</span>
            <ChevronRight aria-hidden="true" className={s.flipRtl} />
          </button>
        </li>
      </ul>
      <EquivalentsDialog open={equivalentOpen} onOpenChange={setEquivalentOpen} initial={initial} />
    </nav>
  );
}
