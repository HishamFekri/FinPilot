"use client";

import { ChevronRight } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type CSSProperties, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { installmentsApi } from "@/lib/installments";
import { localeTag } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { recurringApi, type Occurrence } from "@/lib/recurring";
import { mergeUpcoming, type Stop } from "@/lib/upcoming";
import { todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useUser } from "../auth-provider";
import { useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { ConfirmDialog, moneyIn } from "../recurring/parts";
import h from "./home.module.css";
import { Skeleton, useRemote } from "./remote";

const SHOWN = 4;
// Past this many characters an amount needs a full line, so the timeline runs downward instead.
const WIDE_AMOUNT = 11;

function StopBody({ stop, first }: { stop: Stop; first: boolean }) {
  const t = useTranslations("app.home.upcoming");
  const locale = useLocale() as Locale;
  const date = new Intl.DateTimeFormat(localeTag(locale), {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${stop.date}T12:00:00Z`));
  const incoming = stop.occurrence ? moneyIn(stop.occurrence) : false;
  return (
    <>
      <span className={h.stopDot} data-first={first || undefined} aria-hidden="true" />
      <span className={h.stopDate} data-first={first || undefined}>
        <bdi>{date}</bdi>
      </span>
      <span className={h.stopName}>
        <bdi>{stop.name}</bdi>
      </span>
      <span className={h.stopAmount} data-incoming={incoming || undefined}>
        {stop.amount ? (
          <bdi dir="ltr">{`${incoming ? "+" : ""}${formatMoney(stop.amount, locale)}`}</bdi>
        ) : (
          <span className={h.muted}>{t("varies")}</span>
        )}
      </span>
      {stop.status !== "expected" ? (
        <span className={h.stopStatus} data-status={stop.status}>
          {t(`status.${stop.status}`)}
        </span>
      ) : null}
    </>
  );
}

/**
 * What's expected next: recurring payments, subscriptions and installments on one line. Nothing
 * here is counted as paid; confirming a payment opens the same dialog as the Recurring page.
 */
export function HomeTimeline() {
  const t = useTranslations("app.home.upcoming");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { revision } = useFinance();
  const [confirming, setConfirming] = useState<{ o: Occurrence; n: number } | null>(null);
  const [open, setOpen] = useState(false);
  const today = todayIn(user.time_zone);
  const [state, reload] = useRemote(`${revision}`, async () => {
    const [occurrences, plans] = await Promise.all([
      recurringApi.upcoming(8),
      // Installments are extra here: without them, the recurring payments still show.
      installmentsApi.overview().catch(() => null),
    ]);
    return mergeUpcoming(occurrences, plans?.upcoming ?? [], today);
  });
  const stops = state.kind === "ready" ? state.data : [];
  const shown = stops.slice(0, SHOWN);
  const extra = stops.length - shown.length;
  const wide = shown.some((s) => s.amount && formatMoney(s.amount, locale).length > WIDE_AMOUNT);

  return (
    <section className={`${h.section} ${h.timelineSection}`} aria-labelledby="upcoming-title">
      <div className={h.sectionHead}>
        <div>
          <h2 id="upcoming-title" className={h.sectionTitle}>
            {t("title")}
          </h2>
          <p className={h.sectionLead}>{t("lead")}</p>
        </div>
      </div>
      {state.kind === "loading" ? (
        <Skeleton lines={2} />
      ) : state.kind === "failed" ? (
        <p className={h.muted}>{t("failed")}</p>
      ) : shown.length === 0 ? (
        <p className={h.muted}>
          {t("empty")}{" "}
          <Link href="/app/recurring" className={appStyles.linkBtn}>
            {t("setUp")}
          </Link>
        </p>
      ) : (
        <ol
          className={h.timeline}
          data-wide={wide || undefined}
          style={{ "--stops": shown.length } as CSSProperties}
        >
          {shown.map((stop, i) => (
            <li key={stop.key} className={h.stop} data-status={stop.status}>
              {stop.occurrence ? (
                <button
                  type="button"
                  className={h.stopBody}
                  onClick={() => {
                    setConfirming((prev) => ({ o: stop.occurrence!, n: (prev?.n ?? 0) + 1 }));
                    setOpen(true);
                  }}
                >
                  <StopBody stop={stop} first={i === 0} />
                </button>
              ) : (
                <Link href={`/app/installments/${stop.planId}`} className={h.stopBody}>
                  <StopBody stop={stop} first={i === 0} />
                </Link>
              )}
            </li>
          ))}
        </ol>
      )}
      {state.kind === "ready" && stops.length > 0 ? (
        <Link href="/app/recurring" className={h.quietLink}>
          {extra > 0 ? t("more", { count: extra }) : t("all")}
          <ChevronRight aria-hidden="true" className={fs.flipRtl} />
        </Link>
      ) : null}
      {confirming ? (
        <ConfirmDialog
          key={confirming.n}
          occurrence={confirming.o}
          open={open}
          onClose={() => setOpen(false)}
          onDone={() => {
            setOpen(false);
            reload();
          }}
        />
      ) : null}
    </section>
  );
}
