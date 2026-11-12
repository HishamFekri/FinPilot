"use client";

import { Info, RefreshCw, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Money as MoneyValue } from "@/lib/money";
import type { AccountValue, BalanceGroup } from "@/lib/balances";
import { Money } from "../accounts/parts";
import { Notice, Spinner } from "../ui";
import { useLoad } from "../use-load";
import { CurrencyPicker } from "./display-currency";
import s from "./balances.module.css";

export type TotalBalance = {
  target: string;
  accounts: AccountValue[];
  balances: BalanceGroup;
  missing_rates: string[];
  is_stale: boolean;
  rates: {
    currency: string;
    status: "same" | "ok" | "unavailable";
    rate: string | null;
    rate_date: string | null;
    provider: string | null;
    upstream: string | null;
    is_stale: boolean;
  }[];
};

/** Active accounts only, at the latest reference rates. Display only; nothing is stored. */
export function useTotalBalance(target: string, refreshes = 0) {
  const [own, setRefresh] = useState(0);
  const refresh = own + refreshes;
  const load = useLoad(`total-balance:${target}:${refresh}`, () =>
    api<TotalBalance>(
      `/balances/total?target=${encodeURIComponent(target)}${refresh ? "&refresh=true" : ""}`,
    ),
  );
  return { ...load, refresh: () => setRefresh((n) => n + 1) };
}

/**
 * Total Balance: what the person's active accounts hold, each currency exactly, and an
 * approximate combined figure in a currency they pick. Never debts or installment plans.
 */
export function TotalBalanceCard({
  target,
  onTarget,
  data,
  failed,
  loading,
  onRefresh,
  accountCount,
}: {
  target: string;
  onTarget: (code: string) => void;
  data: TotalBalance | undefined;
  failed: boolean;
  loading: boolean;
  onRefresh: () => void;
  accountCount?: number;
}) {
  const t = useTranslations("app.totalBalance");
  const locale = useLocale() as Locale;
  const id = useId();
  const [showRates, setShowRates] = useState(false);
  const converted = data?.rates.filter((r) => r.status === "ok") ?? [];

  return (
    <section className={s.card} aria-labelledby={`${id}-title`} aria-busy={loading || undefined}>
      <div className={s.head}>
        <h2 id={`${id}-title`} className={s.title}>
          {t("title")}
        </h2>
        <div className={s.controls}>
          <CurrencyPicker value={target} onChange={onTarget} label={t("showIn")} />
          <button
            type="button"
            className={s.iconButton}
            onClick={onRefresh}
            aria-label={t("refresh")}
            title={t("refresh")}
            disabled={loading}
          >
            <RefreshCw aria-hidden="true" />
          </button>
        </div>
      </div>

      {failed ? (
        <Notice tone="error">{t("failed")}</Notice>
      ) : !data ? (
        <p className={s.note}>
          <Spinner /> {t("loading")}
        </p>
      ) : (
        <>
          <div className={s.figure}>
            {data.balances.combined ? (
              <>
                <span className={s.big}>
                  <span className={s.approx}>{"≈"}</span>
                  <Money value={data.balances.combined} />
                </span>
                <span className={s.label}>
                  {t("approximate", { currency: target })}
                  {accountCount !== undefined ? (
                    <span className={s.count}>{t("accounts", { count: accountCount })}</span>
                  ) : null}
                </span>
              </>
            ) : data.balances.by_currency.length === 0 ? (
              <span className={s.missing}>{t("noAccounts")}</span>
            ) : (
              <p className={s.warn} role="status">
                <TriangleAlert aria-hidden="true" />
                {t("noCombined", { codes: data.missing_rates.join(", ") })}
              </p>
            )}
          </div>

          {data.balances.by_currency.length > 1 || !data.balances.combined ? (
            <ul className={s.currencies} aria-label={t("byCurrency")}>
              {data.balances.by_currency.map((m: MoneyValue, i) => (
                <li key={m.currency}>
                  <span dir="ltr">
                    <Money value={m} display="code" />
                  </span>
                  {m.currency !== target ? (
                    <span className={s.equiv}>
                      {data.balances.converted[i] ? (
                        <>
                          {"≈ "}
                          <Money value={data.balances.converted[i]!} />
                        </>
                      ) : (
                        t("noRate")
                      )}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}

          {data.is_stale && data.balances.combined ? (
            <p className={s.warn} role="status">
              <TriangleAlert aria-hidden="true" />
              {t("stale")}
            </p>
          ) : null}

          {converted.length > 0 ? (
            <div>
              <button
                type="button"
                className={s.ratesToggle}
                aria-expanded={showRates}
                aria-controls={`${id}-rates`}
                onClick={() => setShowRates((v) => !v)}
              >
                <Info aria-hidden="true" />
                {showRates ? t("hideRates") : t("showRates")}
              </button>
              {showRates ? (
                <ul id={`${id}-rates`} className={s.rates}>
                  {converted.map((r) => (
                    <li key={r.currency} dir="ltr">
                      {`1 ${target} = ${r.rate} ${r.currency}`}
                      <span className={s.equiv}>
                        {` · ${r.provider ?? r.upstream ?? ""} · ${
                          r.rate_date ? formatDate(`${r.rate_date}T12:00:00Z`, locale, "UTC") : ""
                        }`}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          <p className={s.note}>{t("scope")}</p>
        </>
      )}
    </section>
  );
}
