"use client";

import { useTranslations } from "next-intl";
import { ApiError } from "@/lib/api";
import { useAuth } from "./auth-provider";
import { useFinanceError } from "./finance/parts";

/** Why something couldn't be deleted, for every code the API answers a refused deletion with. */
const CODES = new Set([
  "account_has_entries",
  "account_used_by_recurring",
  "account_used_by_template",
  "balance_not_zero",
  "category_is_default",
  "category_has_entries",
  "category_used_by_budget",
  "category_used_by_recurring",
  "category_used_by_template",
  "rule_has_payments",
  "budget_has_history",
  "plan_has_payments",
  "plan_not_settled",
  "version_conflict",
  "not_found",
]);

/**
 * For confirmation dialogs: turns a failed delete, archive or cancel into a reason in the
 * reader's language (returned, so the dialog stays open and shows it). A signed-out session
 * leaves instead. Never a false success.
 */
export function useRemovalFailure() {
  const t = useTranslations("app.removal.errors");
  const { handleAuthError } = useAuth();
  const fallback = useFinanceError();
  return (error: unknown): string | null => {
    if (handleAuthError(error)) return null;
    if (error instanceof ApiError && error.code && CODES.has(error.code)) {
      return t(error.code as "not_found");
    }
    return fallback(error);
  };
}

/** The same two sentences wherever something can be deleted or archived. */
export function DeleteOrArchive({ canArchive = true }: { canArchive?: boolean }) {
  const t = useTranslations("app.removal");
  return (
    <p>
      {t("deleteMeans")} {canArchive ? t("archiveMeans") : null}
    </p>
  );
}
