"use client";

import { Popover } from "@base-ui/react/popover";
import { Info, RefreshCw, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type CSSProperties, useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { type MonthSummary } from "@/lib/finance";
import { formatDate } from "@/lib/format";
import { fxApi, type MonthSide } from "@/lib/fx";
import { formatMoney, isNegative, type Money as MoneyValue } from "@/lib/money";
import { cn } from "@/lib/utils";
import { Money } from "../accounts/parts";
import { usePortalRoot } from "../app-shell";
import { CurrencyPicker, useDisplayCurrency } from "../balances/display-currency";
import { type TotalBalance, useTotalBalance } from "../balances/total-balance";
import { Spinner } from "../ui";
import { useLoad } from "../use-load";
import h from "./home.module.css";
import { type Remote, Retry } from "./remote";

const MAX_LISTED = 3;

/** Sizes a figure by how long it reads, so a very large one shrinks instead of being cut off. */
const lengthStyle = (text: string) => ({ "--len": text.length }) as CSSProperties;

/**
 * The month's spending or income in the display currency. In its own currency it's exact; converted
 * it's marked approximate; with a rate missing, each currency is listed exactly instead.
 */
function MonthFigure({
  data,
  target,
  tone,
}: {
  data: MonthSide | undefined;
  target: string;
  tone: "spent" | "received";
}) {
  const t = useTranslations("app.home.summary");
  const locale = useLocale() as Locale;
  const className = tone === "spent" ? h.spent : h.received;
  if (!data) return <span className={h.monthValue}>{"—"}</span>;
  const parts = data.parts;
  const first = parts[0];
  const single = parts.length === 1 && first?.status === "same" ? first.spent : null;
  const digits =
    new Intl.NumberFormat("en", { style: "currency", currency: target }).resolvedOptions()
      .maximumFractionDigits ?? 2;
  const zero: MoneyValue = { amount: (0).toFixed(digits), currency: target };
  const exact = parts.length === 0 ? zero : single;
  if (exact || data.combined) {
    const value = exact ?? data.combined!;
    const prefix = exact ? "" : "≈ ";
    return (
      <span
        className={cn(h.monthValue, className)}
        style={lengthStyle(prefix + formatMoney(value, locale))}
      >
        {prefix}
        <Money value={value} />
      </span>
    );
  }
  const extra = parts.length - MAX_LISTED;
  return (
    <span className={h.monthList}>
      {parts.slice(0, MAX_LISTED).map((p) => (
        <Money key={p.currency} value={p.spent} display="code" className={className} />
      ))}
      {extra > 0 ? <span className={h.muted}>{t("more", { count: extra })}</span> : null}
    </span>
  );
}

function Details({
  total,
  spent,
  received,
  summary,
  target,
  onRefresh,
  busy,
}: {
  total: TotalBalance;
  spent: MonthSide | undefined;
  received: MonthSide | undefined;
  summary: Remote<MonthSummary>;
  target: string;
  onRefresh: () => void;
  busy: boolean;
}) {
  const t = useTranslations("app.home.summary");
  const tb = useTranslations("app.totalBalance");
  const home = useTranslations("app.home.month");
  const locale = useLocale() as Locale;
  const root = usePortalRoot();
  const rates = total.rates.filter((r) => r.status === "ok");
  const months = summary.kind === "ready" ? summary.data.currencies : [];
  const trips = months.filter((c) => c.trip_shares);
  const zero = (currency: string): MoneyValue => ({ amount: "0", currency });
  const converted = [...(spent?.parts ?? []), ...(received?.parts ?? [])].some(
    (p) => p.status === "ok",
  );

  return (
    <Popover.Root>
      <Popover.Trigger className={h.detailsButton}>
        <Info aria-hidden="true" />
        {t("details")}
      </Popover.Trigger>
      <Popover.Portal container={root}>
        <Popover.Positioner className={h.popPosition} side="bottom" align="start" sideOffset={8}>
          <Popover.Popup className={h.pop}>
            <div className={h.popHead}>
              <Popover.Title className={h.popTitle}>{t("detailsTitle")}</Popover.Title>
              <button
                type="button"
                className={h.popRefresh}
                onClick={onRefresh}
                disabled={busy}
                aria-label={tb("refresh")}
                title={tb("refresh")}
              >
                {busy ? <Spinner /> : <RefreshCw aria-hidden="true" />}
              </button>
            </div>
            <h4 className={h.popHeading}>{tb("byCurrency")}</h4>
            <ul className={h.popList}>
              {total.balances.by_currency.map((m, i) => (
                <li key={m.currency}>
                  <Money value={m} display="code" />
                  {m.currency !== target ? (
                    <span className={h.muted}>
                      {total.balances.converted[i] ? (
                        <>
                          {"≈ "}
                          <Money value={total.balances.converted[i]!} />
                        </>
                      ) : (
                        tb("noRate")
                      )}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
            {rates.length > 0 ? (
              <>
                <h4 className={h.popHeading}>{t("rates")}</h4>
                <ul className={h.popRates}>
                  {rates.map((r) => (
                    <li key={r.currency}>
                      <bdi dir="ltr">{`1 ${target} = ${r.rate} ${r.currency}`}</bdi>
                      <span className={h.muted}>
                        {`${r.provider ?? r.upstream ?? ""}, ${
                          r.rate_date ? formatDate(`${r.rate_date}T12:00:00Z`, locale, "UTC") : ""
                        }`}
                        {r.is_stale ? ` · ${t("old")}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {months.length > 0 ? (
              <>
                <h4 className={h.popHeading}>{t("monthEach")}</h4>
                <ul className={h.popMonth}>
                  {months.map((c) => (
                    <li key={c.currency}>
                      <span>
                        {t("spent")} <Money value={c.expenses ?? zero(c.currency)} display="code" />
                      </span>
                      <span>
                        {t("received")}{" "}
                        <Money value={c.income ?? zero(c.currency)} display="code" />
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {trips.map((c) => (
              <p key={c.currency} className={h.popNote}>
                {home("trips", {
                  share: formatMoney(c.trip_shares!, locale),
                  paid: formatMoney(c.trip_paid ?? zero(c.currency), locale),
                  back: formatMoney(c.trip_received ?? zero(c.currency), locale),
                })}
              </p>
            ))}
            <p className={h.popNote}>{tb("scope")}</p>
            {converted ? <p className={h.popNote}>{home("separate")}</p> : null}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** Total Balance and the month, the first thing on Home. Active accounts only; display only. */
export function HomeSummary({ month, summary }: { month: string; summary: Remote<MonthSummary> }) {
  const t = useTranslations("app.home.summary");
  const tb = useTranslations("app.totalBalance");
  const locale = useLocale() as Locale;
  const id = useId();
  const [target, setTarget] = useDisplayCurrency();
  const [refreshes, setRefreshes] = useState(0);
  const total = useTotalBalance(target, refreshes);
  const figures = useLoad(`home-month:${target}:${month}:${refreshes}`, () =>
    fxApi.spending({ target, month, refresh: refreshes > 0 }),
  );
  const spent = figures.data;
  const received = figures.data?.received;
  const data = total.data;
  const combined = data?.balances.combined ?? null;
  const exact = data?.rates.every((r) => r.status === "same") ?? false;
  const accounts = data?.accounts.length ?? 0;
  const prefix = combined && !exact ? "≈ " : "";
  const busy = total.loading || figures.loading;

  return (
    <section className={h.summary} aria-labelledby={`${id}-title`} aria-busy={busy || undefined}>
      <div className={h.balance}>
        <h2 id={`${id}-title`} className={h.balanceLabel}>
          {tb("title")}
        </h2>
        {total.error ? (
          <Retry onRetry={total.reload} />
        ) : (
          <>
            <div className={h.balanceRow}>
              {!data ? (
                <span className={h.balanceFigure}>{"—"}</span>
              ) : combined ? (
                <p
                  className={cn(h.balanceFigure, isNegative(combined) && h.negative)}
                  style={lengthStyle(prefix + formatMoney(combined, locale))}
                >
                  {prefix}
                  <Money value={combined} />
                </p>
              ) : data.balances.by_currency.length === 0 ? (
                <p className={h.noAccounts}>{tb("noAccounts")}</p>
              ) : (
                <ul className={h.subtotals} aria-label={tb("byCurrency")}>
                  {data.balances.by_currency.map((m) => (
                    <li key={m.currency} className={cn(isNegative(m) && h.negative)}>
                      <Money value={m} display="code" />
                    </li>
                  ))}
                </ul>
              )}
              <CurrencyPicker
                className={h.currency}
                value={target}
                onChange={(code) => {
                  setTarget(code);
                  setRefreshes(0);
                }}
                label={tb("showIn")}
              />
            </div>
            {data && !combined && data.balances.by_currency.length > 0 ? (
              <p className={h.warn} role="status">
                <TriangleAlert aria-hidden="true" />
                {tb("noCombined", { codes: data.missing_rates.join(", ") })}
              </p>
            ) : null}
            {data && combined && data.is_stale ? (
              <p className={h.warn} role="status">
                <TriangleAlert aria-hidden="true" />
                {tb("stale")}
              </p>
            ) : null}
            {data && accounts > 0 ? (
              <p className={h.balanceSub}>
                {combined ? (
                  <span>
                    {exact
                      ? t("across", { count: accounts })
                      : t("indicative", { count: accounts })}
                  </span>
                ) : null}
                <Details
                  total={data}
                  spent={spent}
                  received={received}
                  summary={summary}
                  target={target}
                  busy={busy}
                  onRefresh={() => setRefreshes((n) => n + 1)}
                />
              </p>
            ) : null}
          </>
        )}
      </div>

      <svg className={h.slash} viewBox="0 0 24 84" preserveAspectRatio="none" aria-hidden="true">
        <line x1="22" y1="2" x2="2" y2="82" vectorEffect="non-scaling-stroke" />
      </svg>

      <dl className={h.month}>
        <div>
          <dt>{t("spent")}</dt>
          <dd>
            <MonthFigure data={spent} target={target} tone="spent" />
          </dd>
        </div>
        <div>
          <dt>{t("received")}</dt>
          <dd>
            <MonthFigure data={received} target={target} tone="received" />
          </dd>
        </div>
      </dl>
    </section>
  );
}
