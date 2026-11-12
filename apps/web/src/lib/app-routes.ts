/**
 * Every page under /{locale}/app. The proxy answers anything else with a real 404 before
 * rendering starts (once a page streams, its status is already 200).
 */
/** Sections still shown as "Soon" with a placeholder page. Every section is built now. */
export const PLANNED_SECTIONS: readonly string[] = [];

const APP_PAGES = new Set<string>([
  "",
  "/accounts",
  "/transactions",
  "/budgets",
  "/recurring",
  "/subscriptions",
  "/calendar",
  "/goals",
  "/installments",
  "/analytics",
  // Old addresses: they forward to the matching Analytics tab.
  "/insights",
  "/reports",
  "/trips",
  "/trips/join",
  "/settings",
  "/settings/categories",
  "/settings/templates",
  "/settings/sessions",
  "/settings/privacy",
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string): boolean {
  return UUID.test(value);
}

/** Whether a path after /{locale}/app is a page that exists. A trailing slash is allowed. */
export function isAppPage(rest: string): boolean {
  const path = rest.replace(/\/$/, "");
  if (APP_PAGES.has(path)) return true;
  const detail = /^\/(accounts|trips|installments)\/([^/]+)$/.exec(path);
  if (detail !== null && isUuid(detail[2] ?? "")) return true;
  const report = /^\/trips\/([^/]+)\/report$/.exec(path);
  return report !== null && isUuid(report[1] ?? "");
}
