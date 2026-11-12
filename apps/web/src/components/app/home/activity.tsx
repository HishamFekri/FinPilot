"use client";

import { ArrowDownLeft, ArrowLeftRight, ArrowUpRight, ChevronRight } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import type { Transaction } from "@/lib/finance";
import { localeTag } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { cn } from "@/lib/utils";
import appStyles from "../app.module.css";
import fs from "../finance/finance.module.css";
import { CategoryIcon, flow, TxnAmount, TypeIcon, useTransactionText } from "../finance/parts";
import h from "./home.module.css";
import { type Remote, Retry, Skeleton } from "./remote";

function shortDate(date: string, locale: Locale) {
  return new Intl.DateTimeFormat(localeTag(locale), {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T12:00:00Z`));
}

function Row({
  txn,
  onOpen,
  fresh,
}: {
  txn: Transaction;
  onOpen: (txn: Transaction) => void;
  fresh: boolean;
}) {
  const t = useTranslations("app.home.activity");
  const v = useTranslations("app.transactions");
  const locale = useLocale() as Locale;
  const text = useTransactionText(txn);
  const direction = flow(txn);
  const date = shortDate(txn.effective_date, locale);
  // The arrow and the sign say which way the money went, so color is never the only signal.
  const Arrow =
    direction === "in" ? ArrowDownLeft : direction === "out" ? ArrowUpRight : ArrowLeftRight;
  const arrowLabel =
    direction === "in" ? t("incoming") : direction === "out" ? t("outgoing") : t("moved");
  return (
    <li className={h.activityItem} data-fresh={fresh || undefined}>
      <button type="button" className={h.activityRow} onClick={() => onOpen(txn)}>
        {txn.category ? (
          <CategoryIcon category={txn.category} variant="bare" />
        ) : (
          <TypeIcon type={txn.type} variant="bare" />
        )}
        <span className={h.activityText}>
          <span className={cn(h.activityTitle, txn.status === "reversed" && fs.voided)}>
            <bdi>{text.title}</bdi>
          </span>
          <span className={h.activityMeta}>
            {text.label ? <bdi>{text.label}</bdi> : null}
            {text.label ? " · " : null}
            <bdi>{text.where}</bdi>
            <span className={h.activityMetaDate}>
              {" · "}
              <bdi>{date}</bdi>
            </span>
            {txn.status === "reversed" ? ` · ${v("voided")}` : null}
          </span>
        </span>
        <span className={h.activityDate}>
          <bdi>{date}</bdi>
        </span>
        <span className={h.activityAmount} data-direction={direction ?? "none"}>
          <span className={h.activityFigure}>
            <Arrow className={h.activityArrow} aria-hidden="true" />
            <span className={appStyles.srOnly}>{arrowLabel}</span>
            <TxnAmount txn={txn} />
          </span>
          {txn.original_amount ? (
            <bdi dir="ltr" className={h.activityOriginal}>
              {formatMoney(txn.original_amount, locale)}
            </bdi>
          ) : null}
        </span>
      </button>
    </li>
  );
}

/** The latest entries. Clicking one opens its details. */
export function HomeActivity({
  state,
  onRetry,
  onOpen,
}: {
  state: Remote<{ items: Transaction[] }>;
  onRetry: () => void;
  onOpen: (txn: Transaction) => void;
}) {
  const t = useTranslations("app.home");
  const a = useTranslations("app.home.activity");
  const items = state.kind === "ready" ? state.data.items : [];
  // Only a row that arrives after the first load is new: the first list appears still.
  const [seen, setSeen] = useState<{
    data: unknown;
    ids: Set<string>;
    fresh: Set<string>;
  } | null>(null);
  if (state.kind === "ready" && seen?.data !== state.data) {
    const ids = new Set(items.map((i) => i.id));
    const fresh = new Set(seen ? [...ids].filter((id) => !seen.ids.has(id)) : []);
    setSeen({ data: state.data, ids, fresh });
  }
  const isFresh = (id: string) => seen?.fresh.has(id) ?? false;

  return (
    <section className={h.section} aria-labelledby="activity-title">
      <div className={h.sectionHead}>
        <h2 id="activity-title" className={h.sectionTitle}>
          {a("title")}
        </h2>
        {items.length > 0 ? (
          <Link href="/app/transactions" className={h.quietLink}>
            {a("viewAll")}
            <ChevronRight aria-hidden="true" className={fs.flipRtl} />
          </Link>
        ) : null}
      </div>
      {state.kind === "loading" ? (
        <Skeleton lines={5} />
      ) : state.kind === "failed" ? (
        <Retry onRetry={onRetry} />
      ) : items.length === 0 ? (
        <div className={h.empty}>
          <p className={h.emptyTitle}>{t("recent.emptyTitle")}</p>
          <p className={h.muted}>{t("recent.emptyBody")}</p>
        </div>
      ) : (
        <ul className={h.activity}>
          {items.map((txn) => (
            <Row key={txn.id} txn={txn} onOpen={onOpen} fresh={isFresh(txn.id)} />
          ))}
        </ul>
      )}
    </section>
  );
}
