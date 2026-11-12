"use client";

import { CircleCheck, CircleDashed, CircleX, Clock, PencilLine } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import type { Locale } from "@/i18n/routing";
import { formatDate } from "@/lib/format";
import type { Expense, TripStatus } from "@/lib/trips";
import { useFinance } from "../finance/finance-provider";
import { CategoryIcon, useCategoryName } from "../finance/parts";
import s from "./trips.module.css";

/** A trip category key's name in the reader's language (trips use the shared default keys). */
export function useTripCategory() {
  const name = useCategoryName();
  return (key: string) => name({ name: null, system_key: key });
}

/** The icon for a category key, borrowed from the reader's own matching default category. */
export function TripCategoryIcon({ categoryKey }: { categoryKey: string }) {
  const { categories } = useFinance();
  const match = categories.find((c) => c.system_key === categoryKey);
  return <CategoryIcon category={match ?? { icon: "circle-ellipsis", color: "slate" }} size="sm" />;
}

export function Initials({ name }: { name: string }) {
  const letters = name
    .trim()
    .split(/\s+/u)
    .slice(0, 2)
    .map((part) => Array.from(part)[0] ?? "")
    .join("")
    .toUpperCase();
  return (
    <span className={s.avatar} aria-hidden="true">
      {letters || "?"}
    </span>
  );
}

export function useDateRange() {
  const locale = useLocale() as Locale;
  const t = useTranslations("app.trips");
  return (start: string | null, end: string | null) => {
    const f = (d: string) => formatDate(`${d}T12:00:00Z`, locale, "UTC");
    if (start && end) return t("dates", { start: f(start), end: f(end) });
    if (start) return t("from", { date: f(start) });
    return null;
  };
}

export function TripStatusTag({ status }: { status: TripStatus }) {
  const t = useTranslations("app.trips.status");
  return (
    <span className={s.tag} data-tone={status === "active" ? "positive" : undefined}>
      {t(status)}
    </span>
  );
}

/** Never colour alone: every state has an icon and words. */
export function ExpenseStatusTag({
  expense,
}: {
  expense: Pick<Expense, "status" | "has_pending_correction" | "is_corrected">;
}) {
  const t = useTranslations("app.trips.expenseStatus");
  if (expense.status === "pending_payer") {
    return (
      <span className={s.tag} data-tone="accent">
        <Clock aria-hidden="true" />
        {t("pending")}
      </span>
    );
  }
  if (expense.status === "rejected") {
    return (
      <span className={s.tag} data-tone="negative">
        <CircleX aria-hidden="true" />
        {t("rejected")}
      </span>
    );
  }
  if (expense.status === "cancelled") {
    return (
      <span className={s.tag}>
        <CircleDashed aria-hidden="true" />
        {t("cancelled")}
      </span>
    );
  }
  if (expense.has_pending_correction) {
    return (
      <span className={s.tag} data-tone="accent">
        <PencilLine aria-hidden="true" />
        {t("changePending")}
      </span>
    );
  }
  if (expense.is_corrected) {
    return (
      <span className={s.tag}>
        <CircleCheck aria-hidden="true" />
        {t("corrected")}
      </span>
    );
  }
  return null;
}

const FSI = String.fromCharCode(0x2068);
const PDI = String.fromCharCode(0x2069);

/** A name inside translated text, in its own direction run, so "Alice" can't reorder an Arabic
 *  sentence around it (Unicode first-strong isolate). */
export function isolate(text: string): string {
  return `${FSI}${text}${PDI}`;
}
