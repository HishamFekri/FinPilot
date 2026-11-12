"use client";

import { Flag } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { formatDateTime } from "@/lib/format";
import { type Dispute, disputesApi, type Expense, type Trip } from "@/lib/trips";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { useFinanceError } from "../finance/parts";
import { Button, Notice } from "../ui";
import { isolate } from "./parts";
import s from "./trips.module.css";

/**
 * Disputes (FINANCIAL_RULES §12.8): a beneficiary flags an approved expense with a reason. It
 * changes no balance. An uninvolved owner or admin can record it as upheld or dismissed; changing
 * the money still takes a correction the payer confirms.
 */

export function DisputedTag({ count }: { count: number }) {
  const t = useTranslations("app.trips.disputes");
  if (count === 0) return null;
  return (
    <span className={s.tag} data-tone="negative">
      <Flag aria-hidden="true" />
      {t("tag", { count })}
    </span>
  );
}

export function DisputeBox({
  trip,
  expense,
  onExpense,
  onChanged,
}: {
  trip: Trip;
  expense: Expense;
  onExpense: (updated: Expense) => void;
  onChanged: (notice: string) => void;
}) {
  const t = useTranslations("app.trips.disputes");
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [raising, setRaising] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const history = expense.dispute_history ?? [];
  const current = expense.current_version;

  const raise = async () => {
    if (!current || !reason.trim()) return;
    setBusy(true);
    setFailure(null);
    try {
      const updated = await disputesApi.raise(
        trip.id,
        expense.id,
        current.id,
        reason.trim(),
        newIdempotencyKey(),
      );
      onExpense(updated);
      setRaising(false);
      setReason("");
      onChanged(t("raised"));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  if (history.length === 0 && !expense.can.dispute) return null;
  return (
    <div className={s.disputeBox}>
      <strong>{t("title")}</strong>
      <p className={s.note}>{t("explainer")}</p>
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      {history.map((d) => (
        <DisputeItem key={d.id} trip={trip} dispute={d} onChanged={onChanged} />
      ))}
      {expense.can.dispute && !raising ? (
        <div>
          <Button size="sm" onClick={() => setRaising(true)}>
            <Flag aria-hidden="true" />
            {t("raise")}
          </Button>
        </div>
      ) : null}
      {raising ? (
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor="dispute-reason">
            {t("reason")}
          </label>
          <textarea
            id="dispute-reason"
            className={appStyles.input}
            dir="auto"
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className={s.actions}>
            <Button
              size="sm"
              variant="primary"
              busy={busy}
              disabled={!reason.trim()}
              onClick={raise}
            >
              {t("send")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRaising(false)}>
              {t("keep")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function DisputeItem({
  trip,
  dispute,
  onChanged,
}: {
  trip: Trip;
  dispute: Dispute;
  onChanged: (notice: string) => void;
}) {
  const t = useTranslations("app.trips.disputes");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [resolving, setResolving] = useState(false);
  const [outcome, setOutcome] = useState<"upheld" | "dismissed">("upheld");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const d = dispute;

  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setFailure(null);
    try {
      await fn();
      onChanged(done);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={s.disputeItem}>
      <span>
        {t("by", {
          name: d.raised_by.user_id === user.id ? t("you") : isolate(d.raised_by.display_name),
          date: isolate(formatDateTime(d.created_at, locale, user.time_zone)),
        })}
        {" · "}
        <span className={s.tag} data-tone={d.status === "open" ? "negative" : undefined}>
          {t(`status.${d.status}`)}
        </span>
      </span>
      <span dir="auto">{`“${d.reason}”`}</span>
      {d.resolution_note ? (
        <span className={s.note}>
          {t("resolution", { name: isolate(d.resolved_by?.display_name ?? "") })}{" "}
          <span dir="auto">{`“${d.resolution_note}”`}</span>
        </span>
      ) : null}
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      <div className={s.actions} style={{ marginBlockStart: 0 }}>
        {d.can.withdraw ? (
          <Button
            size="sm"
            variant="ghost"
            busy={busy}
            onClick={() => run(() => disputesApi.withdraw(trip.id, d.id), t("withdrawn"))}
          >
            {t("withdraw")}
          </Button>
        ) : null}
        {d.can.resolve && !resolving ? (
          <Button size="sm" onClick={() => setResolving(true)}>
            {t("resolve")}
          </Button>
        ) : null}
      </div>
      {resolving ? (
        <div className={appStyles.field}>
          <fieldset className={s.choices}>
            <legend className={appStyles.label}>{t("outcome")}</legend>
            {(["upheld", "dismissed"] as const).map((o) => (
              <label key={o} className={s.choiceRow} style={{ gridTemplateColumns: "auto 1fr" }}>
                <input
                  type="radio"
                  name={`outcome-${d.id}`}
                  checked={outcome === o}
                  onChange={() => setOutcome(o)}
                />
                <span>{t(`outcomes.${o}`)}</span>
              </label>
            ))}
          </fieldset>
          <label className={appStyles.label} htmlFor={`note-${d.id}`}>
            {t("note")}
          </label>
          <input
            id={`note-${d.id}`}
            className={appStyles.input}
            dir="auto"
            maxLength={500}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <p className={s.note}>{t("resolveHint")}</p>
          <div className={s.actions}>
            <Button
              size="sm"
              variant="primary"
              busy={busy}
              disabled={!note.trim()}
              onClick={() =>
                run(() => disputesApi.resolve(trip.id, d.id, outcome, note.trim()), t("resolved"))
              }
            >
              {t("saveResolution")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setResolving(false)}>
              {t("keep")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
