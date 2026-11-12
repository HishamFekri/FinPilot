import {
  ArrowLeftRight,
  CalendarDays,
  CreditCard,
  Target,
  ChartColumn,
  House,
  Landmark,
  type LucideIcon,
  Map as MapIcon,
  PiggyBank,
  Repeat,
  Wallet,
} from "lucide-react";

export type NavKey =
  | "home"
  | "accounts"
  | "transactions"
  | "budgets"
  | "recurring"
  | "subscriptions"
  | "calendar"
  | "goals"
  | "installments"
  | "analytics"
  | "trips"
  | "settings";

/** Sections that exist only as an honest placeholder for now. */
export type PlannedKey = string;

export type NavItem = {
  key: NavKey;
  /** Path inside the app, after /{locale}/app. */
  path: string;
  icon: LucideIcon;
  /** False until the feature exists. Its page says so plainly, and shows no data. */
  built: boolean;
  /** The roadmap step that delivers it (ROADMAP.md), for unbuilt sections. */
  milestone?: string;
};

// PRD.md §6. Trip reports and settlements live inside a trip, so they aren't top level.
// Owner's order: Trips right below Transactions; Insights and Reports are one page.
export const NAV: readonly NavItem[] = [
  { key: "home", path: "", icon: House, built: true },
  { key: "transactions", path: "/transactions", icon: ArrowLeftRight, built: true },
  { key: "trips", path: "/trips", icon: MapIcon, built: true },
  { key: "accounts", path: "/accounts", icon: Wallet, built: true },
  { key: "analytics", path: "/analytics", icon: ChartColumn, built: true },
  // FINANCIAL_RULES.md §4.13: purchases and loans paid in installments.
  { key: "installments", path: "/installments", icon: Landmark, built: true },
  { key: "budgets", path: "/budgets", icon: PiggyBank, built: true },
  // PRD.md §3.8: fixed income, fixed expenses and debts.
  { key: "recurring", path: "/recurring", icon: Repeat, built: true },
  { key: "subscriptions", path: "/subscriptions", icon: CreditCard, built: true },
  { key: "calendar", path: "/calendar", icon: CalendarDays, built: true },
  { key: "goals", path: "/goals", icon: Target, built: true },
];

/**
 * How wide a page's content may grow on large screens; it's centred either way. Reading pages stay
 * narrow, table and chart pages get room, and a trip has space for members beside its expenses.
 */
export type PageWidth = "reading" | "standard" | "wide" | "trip";

export function pageWidth(pathname: string): PageWidth {
  if (pathname.replace(/^\/app/, "").startsWith("/trips/")) return "trip";
  switch (activeKey(pathname)) {
    case "settings":
    case "goals":
      return "reading";
    case "home":
    case "transactions":
    case "analytics":
    case "calendar":
      return "wide";
    default:
      return "standard";
  }
}

/** The sections that have a placeholder page (listed once, in lib/app-routes.ts). */
export { PLANNED_SECTIONS } from "@/lib/app-routes";

/** On phones: four tabs and "More". */
export const TAB_KEYS: readonly NavKey[] = ["home", "accounts", "transactions", "trips"];

/** Which item a path (without the locale) belongs to. */
export function activeKey(pathname: string): NavKey | null {
  const rest = pathname.replace(/^\/app/, "");
  if (rest === "" || rest === "/") return "home";
  if (rest.startsWith("/settings")) return "settings";
  return NAV.find((item) => item.path && rest.startsWith(item.path))?.key ?? null;
}
