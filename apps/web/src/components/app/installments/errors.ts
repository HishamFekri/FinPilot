"use client";

import { useTranslations } from "next-intl";
import { ApiError } from "@/lib/api";
import { useFinanceError } from "../finance/parts";

/** Codes the installment routes answer with, each explained in the reader's language. */
const CODES = new Set([
  "down_payment_too_large",
  "price_required",
  "principal_required",
  "principal_too_small",
  "category_required",
  "account_required",
  "account_currency_mismatch",
  "interest_account_currency",
  "exceeds_remaining",
  "debt_settled",
  "plan_archived",
  "plan_settled",
  "plan_not_settled",
  "plan_has_payments",
  "goal_currency_mismatch",
  "goal_already_linked",
  "goal_follows_plan",
  "too_many_plans",
  "version_conflict",
  "managed_by_installment_plan",
]);

/** A translated reason for any failure; the API's own text is never shown. */
export function useInstallmentError() {
  const t = useTranslations("app.installments.errors");
  const fallback = useFinanceError();
  return (error: unknown): string =>
    error instanceof ApiError && error.code && CODES.has(error.code)
      ? t(error.code as "plan_archived")
      : fallback(error);
}
