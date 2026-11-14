"use client";

import { Map as MapIcon, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import { Link, useRouter } from "@/i18n/navigation";
import { newIdempotencyKey } from "@/lib/accounts";
import { type TripListItem, tripsApi } from "@/lib/trips";
import { cn } from "@/lib/utils";
import { AccountDialog } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { FinanceGate, useFinance } from "../finance/finance-provider";
import fs from "../finance/finance.module.css";
import { useFinanceError } from "../finance/parts";
import { Button, Loading, Notice } from "../ui";
import { isolate, useDateRange } from "./parts";
import { TripPhaseTag } from "./planning";
import s from "./trips.module.css";
import { Select } from "../select";

/**
 * Trips: shared expenses with the people you travel with. Everyone on a trip is a FinPilot user
 * who joined by an invitation link. Trip records are shared with the trip; personal accounts and
 * transactions are never part of a trip (SECURITY.md §4.5).
 */
export function TripsView() {
  return (
    <FinanceGate>
      <Trips />
    </FinanceGate>
  );
}

function Trips() {
  const t = useTranslations("app.trips");
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const range = useDateRange();
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<{
    for: string;
    value?: TripListItem[];
    failed?: boolean;
  } | null>(null);
  const [creating, setCreating] = useState<{ n: number } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const request = `${revision}|${attempt}`;

  useEffect(() => {
    let cancelled = false;
    tripsApi
      .list()
      .then((value) => {
        if (!cancelled) setData({ for: request, value });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setData({ for: request, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [request, handleAuthError]);

  const create = () => {
    setCreating((prev) => ({ n: (prev?.n ?? 0) + 1 }));
    setCreateOpen(true);
  };
  const value = data?.value;

  return (
    <div className={s.page}>
      <div className={s.head}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <Button variant="primary" onClick={create}>
          <Plus aria-hidden="true" />
          {t("create")}
        </Button>
      </div>

      {data?.failed ? (
        <Notice tone="error">
          {t("loadFailed")}{" "}
          <button
            type="button"
            className={appStyles.linkBtn}
            onClick={() => setAttempt((n) => n + 1)}
          >
            {t("retry")}
          </button>
        </Notice>
      ) : !value ? (
        <Loading label={t("loading")} />
      ) : value.length === 0 ? (
        <div className={s.empty}>
          <MapIcon aria-hidden="true" />
          <p className={s.emptyTitle}>{t("emptyTitle")}</p>
          <p className={cn(appStyles.muted, appStyles.small)}>{t("emptyBody")}</p>
          <Button variant="primary" onClick={create}>
            <Plus aria-hidden="true" />
            {t("create")}
          </Button>
        </div>
      ) : (
        <ul className={s.list}>
          {value.map((trip) => {
            const dates = range(trip.start_date, trip.end_date);
            return (
              <li key={trip.id}>
                <Link href={`/app/trips/${trip.id}`} className={s.tripRow}>
                  <span className={s.tripMark}>
                    <MapIcon aria-hidden="true" />
                  </span>
                  <span>
                    <span className={s.tripName} dir="auto">
                      {trip.name}
                    </span>
                    <span className={s.tripSub}>
                      {[trip.destination ? isolate(trip.destination) : null, dates]
                        .filter(Boolean)
                        .join(" · ")}
                      {trip.destination || dates ? " · " : ""}
                      {t("membersCount", { count: trip.members })}
                      {" · "}
                      <bdi dir="ltr">{trip.base_currency}</bdi>
                    </span>
                  </span>
                  <span className={s.tripEnd}>
                    {trip.waiting_on_me > 0 ? (
                      <span className={s.tag} data-tone="accent">
                        {t("waitingCount", { count: trip.waiting_on_me })}
                      </span>
                    ) : null}
                    {trip.member_status !== "active" ? (
                      <span className={s.tag}>{t(`memberStatus.${trip.member_status}`)}</span>
                    ) : null}
                    <TripPhaseTag phase={trip.phase} />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      <p className={s.note}>{t("privacy")}</p>

      {creating ? (
        <CreateTripDialog
          key={`create-${creating.n}`}
          open={createOpen}
          onClose={() => setCreateOpen(false)}
        />
      ) : null}
    </div>
  );
}

function CreateTripDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useTranslations("app.trips.form");
  const user = useUser();
  const router = useRouter();
  const { handleAuthError } = useAuth();
  const { accounts, defaultAccountId } = useFinance();
  const message = useFinanceError();
  const id = useId();
  const home =
    accounts.find((a) => a.id === defaultAccountId)?.currency ?? user.preferred_currency ?? "EUR";
  const [name, setName] = useState("");
  const [destination, setDestination] = useState("");
  const [description, setDescription] = useState("");
  const [currency, setCurrency] = useState(home);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const datesBad = !!start && !!end && end < start;
  const codes = [
    ...new Set([home, ...accounts.map((a) => a.currency), "EUR", "USD", "GBP", "TRY"]),
  ];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (!name.trim() || datesBad) return;
    setBusy(true);
    try {
      const trip = await tripsApi.create(
        {
          name: name.trim(),
          base_currency: currency,
          destination: destination.trim() || null,
          description: description.trim() || null,
          start_date: start || null,
          end_date: end || null,
        },
        key,
      );
      router.push(`/app/trips/${trip.id}`);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
      setBusy(false);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("title")}
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {t("create")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-name`}>
            {t("name")}
          </label>
          <input
            id={`${id}-name`}
            className={appStyles.input}
            dir="auto"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={submitted && !name.trim() ? true : undefined}
            aria-describedby={submitted && !name.trim() ? `${id}-name-error` : undefined}
          />
          {submitted && !name.trim() ? (
            <span id={`${id}-name-error`} className={appStyles.error}>
              {t("nameRequired")}
            </span>
          ) : null}
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-where`}>
            {t("destination")}
          </label>
          <input
            id={`${id}-where`}
            className={appStyles.input}
            dir="auto"
            maxLength={100}
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
          />
        </div>
        <div className={fs.fieldsRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-start`}>
              {t("start")}
            </label>
            <input
              id={`${id}-start`}
              type="date"
              className={appStyles.input}
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </div>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-end`}>
              {t("end")}
            </label>
            <input
              id={`${id}-end`}
              type="date"
              className={appStyles.input}
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              aria-invalid={datesBad || undefined}
            />
          </div>
        </div>
        {datesBad ? <span className={appStyles.error}>{t("datesInvalid")}</span> : null}
        <span className={appStyles.hint}>{t("datesHint")}</span>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-about`}>
            {t("description")}
          </label>
          <textarea
            id={`${id}-about`}
            className={appStyles.input}
            dir="auto"
            rows={2}
            maxLength={500}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-currency`}>
            {t("currency")}
          </label>
          <Select id={`${id}-currency`} value={currency} onValueChange={setCurrency}>
            {codes.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </Select>
          <span className={appStyles.hint}>{t("currencyHint")}</span>
        </div>
      </form>
    </AccountDialog>
  );
}
