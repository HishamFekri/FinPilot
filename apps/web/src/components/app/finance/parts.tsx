"use client";

import {
  ArrowLeftRight,
  Award,
  Baby,
  Book,
  Briefcase,
  Bus,
  Car,
  CircleEllipsis,
  CirclePlus,
  Clapperboard,
  Coffee,
  Dog,
  Droplet,
  Dumbbell,
  Flag,
  Fuel,
  Gift,
  GraduationCap,
  HandCoins,
  HeartPulse,
  House,
  Landmark,
  Laptop,
  type LucideIcon,
  Music,
  PiggyBank,
  Plane,
  Receipt,
  Repeat,
  Shirt,
  Undo2,
  SlidersHorizontal,
  ShoppingBag,
  ShoppingBasket,
  Smartphone,
  Sparkles,
  Tag,
  Utensils,
  Wifi,
  Wrench,
  Zap,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import type { AccountRef, Category, Transaction } from "@/lib/finance";
import { formatDate } from "@/lib/format";
import { formatMoney, type Money as MoneyValue } from "@/lib/money";
import { cn } from "@/lib/utils";
import { Money, useErrorMessage } from "../accounts/parts";
import appStyles from "../app.module.css";
import { bdi } from "../ui";
import s from "./finance.module.css";

/** Every icon a category may use: the backend's allowlist (services/categories.py). */
export const CATEGORY_ICONS: Record<string, LucideIcon> = {
  utensils: Utensils,
  "shopping-basket": ShoppingBasket,
  bus: Bus,
  "shopping-bag": ShoppingBag,
  house: House,
  receipt: Receipt,
  "heart-pulse": HeartPulse,
  "graduation-cap": GraduationCap,
  clapperboard: Clapperboard,
  plane: Plane,
  sparkles: Sparkles,
  repeat: Repeat,
  gift: Gift,
  "circle-ellipsis": CircleEllipsis,
  briefcase: Briefcase,
  laptop: Laptop,
  award: Award,
  "circle-plus": CirclePlus,
  coffee: Coffee,
  car: Car,
  fuel: Fuel,
  baby: Baby,
  dog: Dog,
  dumbbell: Dumbbell,
  music: Music,
  book: Book,
  smartphone: Smartphone,
  wifi: Wifi,
  zap: Zap,
  droplet: Droplet,
  shirt: Shirt,
  wrench: Wrench,
  "piggy-bank": PiggyBank,
  landmark: Landmark,
  "hand-coins": HandCoins,
  tag: Tag,
};

export const CATEGORY_COLORS = [
  "tangerine",
  "amber",
  "green",
  "teal",
  "sky",
  "ocean",
  "indigo",
  "violet",
  "berry",
  "rose",
  "slate",
  "sand",
] as const;

/** The category's mark: its icon on a soft tile of its color. Decorative; the name says it. */
export function CategoryIcon({
  category,
  size = "md",
  variant = "tile",
}: {
  category: Pick<Category, "icon" | "color">;
  size?: "sm" | "md";
  variant?: "tile" | "bare";
}) {
  const Icon = CATEGORY_ICONS[category.icon] ?? Tag;
  return (
    <span
      className={cn(s.catIcon, size === "sm" && s.catIconSm, variant === "bare" && s.catIconBare)}
      data-color={category.color}
      aria-hidden="true"
    >
      <Icon />
    </span>
  );
}

/** A category's name: the person's own, or the default's in the reader's language. */
export function useCategoryName() {
  const t = useTranslations("app.categories.defaults");
  return (category: Pick<Category, "name" | "system_key">): string =>
    category.name ?? (category.system_key ? t(category.system_key as "groceries") : "");
}

/** An account's name: the person's own, or the automatic account's in the reader's language. */
export function useAccountName() {
  const t = useTranslations("app.accounts.defaultNames");
  return (account: Pick<AccountRef, "name" | "system_key">): string =>
    account.name ?? (account.system_key ? t(account.system_key as "cash") : "");
}

const FINANCE_CODES = new Set([
  "category_archived",
  "category_kind_mismatch",
  "currency_required",
  "not_correctable",
  "invalid_state",
  "invalid_cursor",
  "version_conflict",
  "no_change",
  "not_found",
  "category_name_taken",
  "invalid_icon",
  "invalid_color",
  "account_archived",
  "conversion_required",
  "conversion_not_needed",
  "fx_rate_mismatch",
  "fx_rate_stale",
  "fx_unavailable",
  "fx_pair_unsupported",
  "invalid_rate",
  "conversion_rounds_to_zero",
  "exceeds_remaining",
  "debt_settled",
  "debt_has_payments",
  "debt_not_settled",
  "debt_archived",
  "account_amount_required",
  "invalid_window",
  "too_many_targets",
  "invalid_target",
  "amounts_differ",
]);

/** A translated message for any failure on these screens; never the API's own text. */
export function useFinanceError() {
  const t = useTranslations("app.transactions.errors");
  const fallback = useErrorMessage();
  return (error: unknown): string =>
    error instanceof ApiError && error.code && FINANCE_CODES.has(error.code)
      ? t(error.code as "not_found")
      : fallback(error);
}

/**
 * The amount as the person thinks of it: money out is negative, money in is positive. Only the
 * sign is added to the API's decimal string; no arithmetic happens here (M5).
 */
export function flow(txn: Transaction): "in" | "out" | null {
  if (txn.type === "income" || txn.type === "trip_settlement_received" || txn.type === "refund")
    return "in";
  if (
    txn.type === "expense" ||
    txn.type === "trip_expense_payment" ||
    txn.type === "trip_settlement_paid"
  )
    return "out";
  if (txn.type === "adjustment") return txn.direction === "increase" ? "in" : "out";
  if (txn.debt && txn.account) {
    // Borrowing and being repaid bring money in; lending and repaying send it out.
    const owe = txn.debt.direction === "i_owe";
    return (txn.type === "debt_start") === owe ? "in" : "out";
  }
  return null;
}

export function signedAmount(txn: Transaction): MoneyValue {
  return flow(txn) === "out" ? { ...txn.amount, amount: `-${txn.amount.amount}` } : txn.amount;
}

const TYPE_ICON: Record<Transaction["type"], LucideIcon> = {
  income: CirclePlus,
  expense: CircleEllipsis,
  transfer: ArrowLeftRight,
  adjustment: SlidersHorizontal,
  opening_balance: Flag,
  debt_start: HandCoins,
  debt_payment: HandCoins,
  trip_expense_payment: Plane,
  trip_settlement_paid: Plane,
  trip_settlement_received: Plane,
  refund: Undo2,
};

export function TypeIcon({
  type,
  variant = "tile",
}: {
  type: Transaction["type"];
  variant?: "tile" | "bare";
}) {
  const Icon = TYPE_ICON[type];
  return (
    <span
      className={cn(s.catIcon, variant === "bare" && s.catIconBare)}
      data-color="slate"
      aria-hidden="true"
    >
      <Icon />
    </span>
  );
}

export function TxnAmount({ txn, className }: { txn: Transaction; className?: string }) {
  const direction = flow(txn);
  const incoming = direction === "in";
  const signed = direction !== null && txn.type !== "transfer" && txn.type !== "opening_balance";
  return (
    <Money
      value={signedAmount(txn)}
      signed={signed}
      className={cn(className, incoming && s.in, txn.status === "reversed" && s.voided)}
    />
  );
}

/** What a transaction is called in a list: its title, what that didn't say, and where. */
export function useTransactionText(txn: Transaction) {
  const t = useTranslations("app.transactions");
  const categoryName = useCategoryName();
  const accountName = useAccountName();
  const kind = t(`types.${txn.type}`);
  const title =
    txn.description ||
    (txn.category
      ? categoryName(txn.category)
      : txn.debt
        ? `${kind} · ${txn.debt.counterparty}`
        : kind);
  // The second line adds what the title didn't say: the category (or the kind), and the account.
  const label = txn.description ? (txn.category ? categoryName(txn.category) : kind) : null;
  const where =
    txn.type === "transfer" && txn.counter_account
      ? t.rich("transferBetween", {
          from: txn.account ? accountName(txn.account) : "",
          to: accountName(txn.counter_account),
          bdi,
        })
      : txn.account
        ? accountName(txn.account)
        : (txn.debt?.counterparty ?? "");
  return { title, label, where };
}

/** One line in a list: what it was, where it came from, and how much. Opens the detail. */
export function TransactionRow({
  txn,
  onOpen,
  showDate = false,
}: {
  txn: Transaction;
  onOpen: (txn: Transaction) => void;
  showDate?: boolean;
}) {
  const t = useTranslations("app.transactions");
  const locale = useLocale() as Locale;
  const { title, label, where } = useTransactionText(txn);
  const date = showDate ? formatDate(`${txn.effective_date}T12:00:00Z`, locale, "UTC") : null;

  return (
    <button type="button" className={s.row} onClick={() => onOpen(txn)}>
      {txn.category ? <CategoryIcon category={txn.category} /> : <TypeIcon type={txn.type} />}
      <span className={s.rowText}>
        <span className={cn(s.rowTitle, txn.status === "reversed" && s.voided)} dir="auto">
          {title}
        </span>
        <span className={s.rowMeta}>
          {label ? <bdi>{label}</bdi> : null}
          {label ? " · " : null}
          <bdi>{where}</bdi>
          {date ? (
            <>
              {" · "}
              <bdi>{date}</bdi>
            </>
          ) : null}
          {txn.status === "reversed" ? ` · ${t("voided")}` : null}
        </span>
      </span>
      <span className={s.amountCell}>
        <TxnAmount txn={txn} className={s.rowAmount} />
        {txn.original_amount ? (
          <bdi dir="ltr" className={s.rowOriginal}>
            {formatMoney(txn.original_amount, locale)}
          </bdi>
        ) : null}
      </span>
    </button>
  );
}

/**
 * The category choice: a radio group of icon chips, app-controlled (never a long native list).
 * The first few show; the rest open in place. The chosen one is always visible.
 */
export function CategoryPicker({
  categories,
  value,
  onChange,
  invalid,
  describedBy,
  limit = 9,
  name,
  legend,
}: {
  categories: Category[];
  value: string | null;
  onChange: (id: string) => void;
  invalid?: boolean;
  describedBy?: string;
  limit?: number;
  name: string;
  legend: string;
}) {
  const t = useTranslations("app.quickAdd");
  const categoryName = useCategoryName();
  const [expanded, setExpanded] = useState(false);
  const overflow = categories.length > limit + 1;
  const shown =
    !overflow || expanded ? categories : categories.filter((c, i) => i < limit || c.id === value);
  const hidden = categories.length - shown.length;

  return (
    <fieldset
      className={s.chips}
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
    >
      <legend className={appStyles.srOnly}>{legend}</legend>
      {shown.map((category) => (
        <label key={category.id} className={s.chip}>
          <input
            type="radio"
            name={name}
            value={category.id}
            checked={value === category.id}
            onChange={() => onChange(category.id)}
          />
          <CategoryIcon category={category} size="sm" />
          <span className={s.chipLabel} dir="auto">
            {categoryName(category)}
          </span>
        </label>
      ))}
      {overflow ? (
        <button
          type="button"
          className={cn(s.chip, s.moreChip)}
          aria-expanded={expanded}
          onClick={() => setExpanded((open) => !open)}
        >
          {expanded ? t("fewer") : t("more", { count: hidden })}
        </button>
      ) : null}
    </fieldset>
  );
}
