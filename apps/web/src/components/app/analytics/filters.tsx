"use client";

import { useTranslations } from "next-intl";
import { useId } from "react";
import { type AllocationView, type PeriodKey, periodRange } from "@/lib/analytics";
import appStyles from "../app.module.css";
import { useAccountName } from "../finance/parts";
import { useFinance } from "../finance/finance-provider";
import s from "./analytics.module.css";
import { Select } from "../select";

export type Period = { key: PeriodKey; from: string; to: string };

export const PRESETS: readonly PeriodKey[] = [
  "today",
  "week",
  "month",
  "lastMonth",
  "all",
  "custom",
];

/** The query for a period: preset dates, or the custom range as typed. */
export function periodQuery(
  period: Period,
  today: string,
): { from?: string; to?: string; all_history?: boolean } {
  if (period.key === "custom")
    return { from: period.from || undefined, to: period.to || undefined };
  return periodRange(period.key, today);
}

export function customInvalid(period: Period): boolean {
  return period.key === "custom" && !!period.from && !!period.to && period.from > period.to;
}

/** Period presets and, for "Custom", two date fields. */
export function PeriodPicker({
  value,
  onChange,
  today,
  presets = PRESETS,
}: {
  value: Period;
  onChange: (next: Period) => void;
  today: string;
  presets?: readonly PeriodKey[];
}) {
  const t = useTranslations("app.reports.period");
  const id = useId();
  const invalid = customInvalid(value);
  return (
    <>
      <div className={s.presets} role="group" aria-label={t("label")}>
        {presets.map((key) => (
          <button
            key={key}
            type="button"
            className={s.preset}
            aria-pressed={value.key === key}
            onClick={() => {
              if (key === "custom") {
                const range = periodQuery(value, today);
                onChange({
                  key,
                  from: value.from || range.from || today,
                  to: value.to || range.to || today,
                });
              } else onChange({ ...value, key });
            }}
          >
            {t(key)}
          </button>
        ))}
      </div>
      {value.key === "custom" ? (
        <>
          <div className={s.filterField}>
            <label className={s.filterLabel} htmlFor={`${id}-from`}>
              {t("from")}
            </label>
            <input
              id={`${id}-from`}
              type="date"
              className={appStyles.input}
              value={value.from}
              max={today}
              onChange={(e) => onChange({ ...value, from: e.target.value })}
              aria-invalid={invalid || undefined}
            />
          </div>
          <div className={s.filterField}>
            <label className={s.filterLabel} htmlFor={`${id}-to`}>
              {t("to")}
            </label>
            <input
              id={`${id}-to`}
              type="date"
              className={appStyles.input}
              value={value.to}
              max={today}
              onChange={(e) => onChange({ ...value, to: e.target.value })}
              aria-invalid={invalid || undefined}
              aria-describedby={invalid ? `${id}-error` : undefined}
            />
          </div>
          {invalid ? (
            <span id={`${id}-error`} className={appStyles.error} role="alert">
              {t("invalid")}
            </span>
          ) : null}
        </>
      ) : null}
    </>
  );
}

export function AccountSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const t = useTranslations("app.reports.filters");
  const { accounts } = useFinance();
  const accountName = useAccountName();
  const id = useId();
  return (
    <div className={s.filterField}>
      <label className={s.filterLabel} htmlFor={id}>
        {t("account")}
      </label>
      <Select id={id} value={value} onValueChange={(value) => onChange(value)}>
        <option value="">{t("allAccounts")}</option>
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {`${accountName(a)} · ${a.currency}`}
          </option>
        ))}
      </Select>
    </div>
  );
}

export function CurrencySelect({
  value,
  onChange,
  codes,
  allowAll,
}: {
  value: string;
  onChange: (code: string) => void;
  codes: string[];
  allowAll?: boolean;
}) {
  const t = useTranslations("app.reports.filters");
  const id = useId();
  return (
    <div className={s.filterField}>
      <label className={s.filterLabel} htmlFor={id}>
        {t("currency")}
      </label>
      <Select id={id} value={value} onValueChange={(value) => onChange(value)}>
        {allowAll ? <option value="">{t("allCurrencies")}</option> : null}
        {codes.map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </Select>
    </div>
  );
}

/**
 * Whether spending includes the person's trip shares (FINANCIAL_RULES §11.10). A view only: it
 * never changes balances, cash flow, income, budgets or trip figures.
 */
export function AllocationSelect({
  value,
  onChange,
}: {
  value: AllocationView;
  onChange: (view: AllocationView) => void;
}) {
  const t = useTranslations("app.reports.filters");
  const id = useId();
  return (
    <div className={s.filterField}>
      <label className={s.filterLabel} htmlFor={id}>
        {t("tripShares")}
      </label>
      <Select id={id} value={value} onValueChange={(value) => onChange(value as AllocationView)}>
        <option value="include">{t("allocation.include")}</option>
        <option value="exclude">{t("allocation.exclude")}</option>
        <option value="only">{t("allocation.only")}</option>
      </Select>
    </div>
  );
}
