"use client";

import { BedDouble, Link2, MapPin, NotebookPen, Pencil, Plane, Plus, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { formatDate } from "@/lib/format";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import {
  type BookingStatus,
  type PlanItem,
  type PlanItemBody,
  type PlanKind,
  planningApi,
  type TransportMode,
  type Trip,
  type TripPhase,
  tripsApi,
} from "@/lib/trips";
import { AccountDialog, AmountField, Money } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { ConfirmDialog } from "../confirm-dialog";
import { useFinance } from "../finance/finance-provider";
import { useFinanceError } from "../finance/parts";
import { Select } from "../select";
import { Button, Loading, Notice, Tag } from "../ui";
import { useLoad } from "../use-load";
import s from "./trips.module.css";

const KIND_ICONS = {
  accommodation: BedDouble,
  transport: Plane,
  activity: MapPin,
  note: NotebookPen,
  link: Link2,
} as const;

/** What the trip is now, in words (and a colour only alongside them). */
export function TripPhaseTag({ phase }: { phase: TripPhase }) {
  const t = useTranslations("app.trips.phases");
  const tone = phase === "ongoing" ? "positive" : phase === "upcoming" ? "accent" : "neutral";
  return <Tag tone={tone}>{t(phase)}</Tag>;
}

function useAmount(currency: string) {
  const locale = useLocale() as Locale;
  const { currencies } = useFinance();
  const exponent = currencies.find((c) => c.code === currency)?.exponent ?? 2;
  return {
    exponent,
    value: (text: string) => {
      const parsed = text.trim() ? parseAmount(text, locale, exponent) : null;
      return parsed?.ok ? parsed.amount : null;
    },
  };
}

function PlanItemDialog({
  trip,
  item,
  open,
  onOpenChange,
  onSaved,
}: {
  trip: Trip;
  item: PlanItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const t = useTranslations("app.trips.plan");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const id = useId();
  const money = useAmount(trip.base_currency);
  const [kind, setKind] = useState<PlanKind>(item?.kind ?? "accommodation");
  const [title, setTitle] = useState(item?.title ?? "");
  const [mode, setMode] = useState<TransportMode | "">(item?.transport_mode ?? "");
  const [startsOn, setStartsOn] = useState(item?.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(item?.ends_on ?? "");
  const [status, setStatus] = useState<BookingStatus>(item?.booking_status ?? "planned");
  const [estimated, setEstimated] = useState(
    item?.estimated ? editableAmount(item.estimated.amount, locale) : "",
  );
  const [deposit, setDeposit] = useState(
    item?.deposit ? editableAmount(item.deposit.amount, locale) : "",
  );
  const [reference, setReference] = useState(item?.reference ?? "");
  const [url, setUrl] = useState(item?.url ?? "");
  const [notes, setNotes] = useState(item?.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const datesBad = Boolean(startsOn && endsOn && endsOn < startsOn);
  const urlBad = Boolean(url.trim() && !/^https:\/\/\S+$/.test(url.trim()));

  const submit = async () => {
    setSubmitted(true);
    setFailure(null);
    if (!title.trim() || datesBad || urlBad) return;
    const body: PlanItemBody = {
      kind,
      title: title.trim(),
      transport_mode: kind === "transport" && mode ? mode : null,
      starts_on: startsOn || null,
      ends_on: endsOn || null,
      booking_status: status,
      estimated: money.value(estimated),
      deposit: money.value(deposit),
      reference: reference.trim() || null,
      url: url.trim() || null,
      notes: notes.trim() || null,
    };
    setBusy(true);
    try {
      if (item) await planningApi.update(trip.id, item.id, item.version, body);
      else await planningApi.add(trip.id, body);
      onSaved();
      onOpenChange(false);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  const dateLabels = {
    accommodation: [t("checkIn"), t("checkOut")],
    transport: [t("departure"), t("return")],
    activity: [t("start"), t("end")],
    note: [t("start"), t("end")],
    link: [t("start"), t("end")],
  }[kind];

  return (
    <AccountDialog
      open={open}
      onOpenChange={onOpenChange}
      title={item ? t("editTitle") : t("addTitle")}
      lead={t("lead")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button variant="primary" busy={busy} onClick={() => void submit()}>
            {t("save")}
          </Button>
        </>
      }
    >
      <div className={appStyles.form}>
        <div className={appStyles.fieldRow}>
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-kind`}>
              {t("kind")}
            </label>
            <Select id={`${id}-kind`} value={kind} onValueChange={(v) => setKind(v as PlanKind)}>
              {(["accommodation", "transport", "activity", "note", "link"] as const).map((k) => (
                <option key={k} value={k}>
                  {t(`kinds.${k}`)}
                </option>
              ))}
            </Select>
          </div>
          {kind === "transport" ? (
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-mode`}>
                {t("mode")}
              </label>
              <Select
                id={`${id}-mode`}
                value={mode}
                onValueChange={(v) => setMode(v as TransportMode)}
                placeholder={t("chooseMode")}
              >
                {(["flight", "train", "bus", "car", "ferry", "other"] as const).map((m) => (
                  <option key={m} value={m}>
                    {t(`modes.${m}`)}
                  </option>
                ))}
              </Select>
            </div>
          ) : (
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-status`}>
                {t("status")}
              </label>
              <Select
                id={`${id}-status`}
                value={status}
                onValueChange={(v) => setStatus(v as BookingStatus)}
              >
                {(["idea", "planned", "booked", "cancelled"] as const).map((b) => (
                  <option key={b} value={b}>
                    {t(`statuses.${b}`)}
                  </option>
                ))}
              </Select>
            </div>
          )}
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-title`}>
            {t(`titleLabel.${kind}`)}
          </label>
          <input
            id={`${id}-title`}
            className={appStyles.input}
            dir="auto"
            maxLength={100}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            aria-invalid={(submitted && !title.trim()) || undefined}
          />
        </div>
        {kind === "transport" ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-status2`}>
              {t("status")}
            </label>
            <Select
              id={`${id}-status2`}
              value={status}
              onValueChange={(v) => setStatus(v as BookingStatus)}
            >
              {(["idea", "planned", "booked", "cancelled"] as const).map((b) => (
                <option key={b} value={b}>
                  {t(`statuses.${b}`)}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        {kind !== "note" && kind !== "link" ? (
          <div className={appStyles.fieldRow}>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-from`}>
                {dateLabels[0]}
              </label>
              <input
                id={`${id}-from`}
                type="date"
                className={appStyles.input}
                value={startsOn}
                onChange={(e) => setStartsOn(e.target.value)}
              />
            </div>
            <div className={appStyles.field}>
              <label className={appStyles.label} htmlFor={`${id}-to`}>
                {dateLabels[1]}
              </label>
              <input
                id={`${id}-to`}
                type="date"
                className={appStyles.input}
                value={endsOn}
                onChange={(e) => setEndsOn(e.target.value)}
                aria-invalid={datesBad || undefined}
              />
            </div>
          </div>
        ) : null}
        {datesBad ? <span className={appStyles.error}>{t("datesInvalid")}</span> : null}
        {kind !== "note" && kind !== "link" ? (
          <div className={appStyles.fieldRow}>
            <AmountField
              label={t("estimated")}
              hint={t("estimatedHint")}
              currency={trip.base_currency}
              exponent={money.exponent}
              value={estimated}
              onChange={setEstimated}
              showErrors={false}
              required={false}
            />
            {kind === "accommodation" ? (
              <AmountField
                label={t("deposit")}
                hint={t("depositHint")}
                currency={trip.base_currency}
                exponent={money.exponent}
                value={deposit}
                onChange={setDeposit}
                showErrors={false}
                required={false}
              />
            ) : null}
          </div>
        ) : null}
        {kind === "accommodation" || kind === "transport" ? (
          <div className={appStyles.field}>
            <label className={appStyles.label} htmlFor={`${id}-ref`}>
              {t("reference")}
            </label>
            <input
              id={`${id}-ref`}
              className={appStyles.input}
              dir="ltr"
              maxLength={60}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
            <span className={appStyles.hint}>{t("referenceHint")}</span>
          </div>
        ) : null}
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-url`}>
            {t("url")}
          </label>
          <input
            id={`${id}-url`}
            className={appStyles.input}
            dir="ltr"
            inputMode="url"
            maxLength={500}
            placeholder="https://"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            aria-invalid={urlBad || undefined}
          />
          {urlBad ? <span className={appStyles.error}>{t("urlInvalid")}</span> : null}
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-notes`}>
            {t("notes")}
          </label>
          <textarea
            id={`${id}-notes`}
            className={appStyles.input}
            dir="auto"
            rows={2}
            maxLength={1000}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </div>
    </AccountDialog>
  );
}

/** Bookings, journeys and ideas, shared with members. Estimates are never spending. */
export function PlanCard({ trip, writable }: { trip: Trip; writable: boolean }) {
  const t = useTranslations("app.trips.plan");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const [stamp, setStamp] = useState(0);
  const { data, error } = useLoad(`plan:${trip.id}:${stamp}`, () => planningApi.items(trip.id));
  const [editing, setEditing] = useState<PlanItem | "new" | null>(null);
  const [removing, setRemoving] = useState<PlanItem | null>(null);
  const reload = () => setStamp((n) => n + 1);
  const date = (d: string | null) => (d ? formatDate(`${d}T12:00:00Z`, locale, "UTC") : null);

  return (
    <section className={s.card} aria-labelledby="plan-title">
      <div className={s.cardHead}>
        <h2 id="plan-title" className={s.cardTitle}>
          {t("title")}
        </h2>
        {writable ? (
          <Button size="sm" onClick={() => setEditing("new")}>
            <Plus aria-hidden="true" />
            {t("add")}
          </Button>
        ) : null}
      </div>
      <div className={`${s.cardBody} ${s.cardStack}`}>
        {error ? (
          <Notice tone="error">{t("failed")}</Notice>
        ) : !data ? (
          <Loading />
        ) : data.length === 0 ? (
          <p className={s.note}>{t("empty")}</p>
        ) : (
          <ul className={s.planItems}>
            {data.map((item) => {
              const Icon = KIND_ICONS[item.kind];
              const range = [date(item.starts_on), date(item.ends_on)].filter(Boolean).join(" – ");
              return (
                <li
                  key={item.id}
                  className={s.planItem}
                  data-cancelled={item.booking_status === "cancelled" || undefined}
                >
                  <span className={s.planIcon} aria-hidden="true">
                    <Icon />
                  </span>
                  <span className={s.planText}>
                    <span className={s.planTitle} dir="auto">
                      {item.title}
                    </span>
                    <span className={s.planMeta}>
                      {[
                        item.transport_mode ? t(`modes.${item.transport_mode}`) : null,
                        range || null,
                        item.kind !== "note" && item.kind !== "link"
                          ? t(`statuses.${item.booking_status}`)
                          : null,
                        item.reference ? `#${item.reference}` : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                    {item.url ? (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className={s.planLink}
                        dir="ltr"
                      >
                        {item.url.replace(/^https:\/\//, "").slice(0, 60)}
                      </a>
                    ) : null}
                    {item.notes ? (
                      <span className={s.planNotes} dir="auto">
                        {item.notes}
                      </span>
                    ) : null}
                  </span>
                  <span className={s.planAmounts}>
                    {item.estimated ? (
                      <span>
                        {t("estimateShort", { amount: formatMoney(item.estimated, locale) })}
                      </span>
                    ) : null}
                    {item.deposit ? (
                      <span className={s.note}>
                        {t("depositShort", { amount: formatMoney(item.deposit, locale) })}
                      </span>
                    ) : null}
                    {item.expense_id ? <Tag tone="positive">{t("recorded")}</Tag> : null}
                  </span>
                  {writable && item.can_edit ? (
                    <span className={s.planActions}>
                      <button
                        type="button"
                        className={s.iconAction}
                        aria-label={t("edit", { title: item.title })}
                        onClick={() => setEditing(item)}
                      >
                        <Pencil aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className={s.iconAction}
                        aria-label={t("remove", { title: item.title })}
                        onClick={() => setRemoving(item)}
                      >
                        <Trash2 aria-hidden="true" />
                      </button>
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        <p className={s.note}>{t("explainer")}</p>
      </div>
      {editing ? (
        <PlanItemDialog
          key={editing === "new" ? "new" : editing.id}
          trip={trip}
          item={editing === "new" ? null : editing}
          open
          onOpenChange={(open) => !open && setEditing(null)}
          onSaved={reload}
        />
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t("removeTitle")}
        body={<p>{t("removeBody")}</p>}
        confirm={t("removeConfirm")}
        onConfirm={async () => {
          if (!removing) return null;
          try {
            await planningApi.remove(trip.id, removing.id);
            reload();
            return null;
          } catch (e) {
            if (handleAuthError(e)) return null;
            return message(e);
          }
        }}
      />
    </section>
  );
}

function BudgetBar({ percent }: { percent: string | null }) {
  const value = percent === null ? 0 : Math.min(Number(percent), 100);
  return (
    <span className={s.budgetTrack} aria-hidden="true">
      <span
        className={s.budgetFill}
        data-over={percent !== null && Number(percent) > 100 ? "" : undefined}
        style={{ inlineSize: `${value}%` }}
      />
    </span>
  );
}

/**
 * The group's shared budget (admins set it; members see it) and the caller's private budget
 * (only they ever see it), each against the right figure: approved spending for the group, the
 * caller's own share for them. Cash paid is shown separately: it isn't spending.
 */
export function TripBudgetsCard({
  trip,
  onTripChanged,
}: {
  trip: Trip;
  onTripChanged: () => void;
}) {
  const t = useTranslations("app.trips.budgets");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const money = useAmount(trip.base_currency);
  const [stamp, setStamp] = useState(0);
  const { data, error } = useLoad(`trip-analytics:${trip.id}:${stamp}:${trip.version}`, () =>
    planningApi.analytics(trip.id),
  );
  const [editing, setEditing] = useState<"shared" | "mine" | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const open = (which: "shared" | "mine") => {
    const current = which === "shared" ? trip.shared_budget : data?.my_budget;
    setText(current ? editableAmount(current.amount, locale) : "");
    setFailure(null);
    setEditing(which);
  };

  const save = async () => {
    setBusy(true);
    setFailure(null);
    try {
      const amount = money.value(text);
      if (editing === "shared") {
        await tripsApi.update(trip.id, trip.version, { shared_budget: amount });
        onTripChanged();
      } else {
        await planningApi.setMyBudget(trip.id, amount);
      }
      setStamp((n) => n + 1);
      setEditing(null);
    } catch (e) {
      if (!handleAuthError(e)) setFailure(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={s.card} aria-labelledby="trip-budgets-title">
      <div className={s.cardHead}>
        <h2 id="trip-budgets-title" className={s.cardTitle}>
          {t("title")}
        </h2>
      </div>
      <div className={`${s.cardBody} ${s.cardStack}`}>
        {error ? <Notice tone="error">{t("failed")}</Notice> : null}
        {!data ? (
          <Loading />
        ) : (
          <>
            <div className={s.budgetBlock}>
              <div className={s.budgetHead}>
                <strong>{t("shared")}</strong>
                {trip.can.admin && trip.status !== "archived" ? (
                  <button type="button" className={s.linkButton} onClick={() => open("shared")}>
                    {data.shared_budget ? t("change") : t("set")}
                  </button>
                ) : null}
              </div>
              {data.shared_budget ? (
                <>
                  <p className={s.budgetLine}>
                    {t("spentOf", {
                      spent: formatMoney(data.actual, locale),
                      budget: formatMoney(data.shared_budget, locale),
                    })}
                  </p>
                  <BudgetBar percent={data.shared_percent} />
                  <p className={s.note}>
                    {data.shared_remaining && data.shared_remaining.amount.startsWith("-")
                      ? t("over", {
                          amount: formatMoney(
                            {
                              ...data.shared_remaining,
                              amount: data.shared_remaining.amount.slice(1),
                            },
                            locale,
                          ),
                        })
                      : t("remainingAmount", {
                          amount: formatMoney(data.shared_remaining!, locale),
                        })}
                  </p>
                </>
              ) : (
                <p className={s.note}>{trip.can.admin ? t("noSharedAdmin") : t("noShared")}</p>
              )}
            </div>
            <div className={s.budgetBlock}>
              <div className={s.budgetHead}>
                <strong>{t("mine")}</strong>
                <button type="button" className={s.linkButton} onClick={() => open("mine")}>
                  {data.my_budget ? t("change") : t("set")}
                </button>
              </div>
              <p className={s.note}>{t("private")}</p>
              <p className={s.budgetLine}>
                {data.my_budget
                  ? t("spentOf", {
                      spent: formatMoney(data.my_share, locale),
                      budget: formatMoney(data.my_budget, locale),
                    })
                  : t("myShare", { amount: formatMoney(data.my_share, locale) })}
              </p>
              {data.my_budget ? <BudgetBar percent={data.my_percent} /> : null}
              <p className={s.note}>
                {t("cashPaid", { amount: formatMoney(data.my_paid, locale) })}
              </p>
            </div>
          </>
        )}
      </div>
      <AccountDialog
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        title={editing === "shared" ? t("sharedTitle") : t("mineTitle")}
        lead={editing === "shared" ? t("sharedLead") : t("mineLead")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>
              {t("cancel")}
            </Button>
            <Button variant="primary" busy={busy} onClick={() => void save()}>
              {t("save")}
            </Button>
          </>
        }
      >
        <div className={appStyles.form}>
          <AmountField
            label={t("amount")}
            hint={t("clearHint")}
            currency={trip.base_currency}
            exponent={money.exponent}
            value={text}
            onChange={setText}
            showErrors={false}
            required={false}
          />
          {failure ? <Notice tone="error">{failure}</Notice> : null}
        </div>
      </AccountDialog>
    </section>
  );
}

/** Planned versus actual, where the money went, and what's still open. */
export function TripAnalyticsCard({ trip }: { trip: Trip }) {
  const t = useTranslations("app.trips.analytics");
  const categories = useTranslations("app.categories.defaults");
  const locale = useLocale() as Locale;
  const { data, error } = useLoad(`trip-analytics-card:${trip.id}:${trip.version}`, () =>
    planningApi.analytics(trip.id),
  );
  if (error) return <Notice tone="error">{t("failed")}</Notice>;
  if (!data) return null;
  const max = Math.max(1, ...data.daily.map((d) => Number(d.amount.amount)));
  const label = (key: string) =>
    categories.has(key as "groceries") ? categories(key as "groceries") : key;
  return (
    <section className={s.card} aria-labelledby="trip-analytics-title">
      <div className={s.cardHead}>
        <h2 id="trip-analytics-title" className={s.cardTitle}>
          {t("title")}
        </h2>
      </div>
      <div className={`${s.cardBody} ${s.cardStack}`}>
        <dl className={s.statGrid}>
          <div>
            <dt>{t("planned")}</dt>
            <dd>
              <Money value={data.planned} />
            </dd>
          </div>
          <div>
            <dt>{t("actual")}</dt>
            <dd>
              <Money value={data.actual} />
            </dd>
          </div>
          <div>
            <dt>{t("perMember", { count: data.member_count })}</dt>
            <dd>
              <Money value={data.cost_per_member} />
            </dd>
          </div>
          <div>
            <dt>{t("outstanding")}</dt>
            <dd>
              <Money value={data.outstanding} />
            </dd>
          </div>
        </dl>
        <p className={s.note}>{t("plannedNote")}</p>
        {data.actual_by_kind.length ? (
          <ul className={s.kindList} aria-label={t("byKind")}>
            {data.actual_by_kind.map((k) => (
              <li key={k.key}>
                <span>{t(`kinds.${k.key as "other"}`)}</span>
                <Money value={k.amount} />
              </li>
            ))}
          </ul>
        ) : null}
        {data.by_category.length ? (
          <>
            <h3 className={s.subhead}>{t("topCategories")}</h3>
            <ul className={s.kindList}>
              {data.by_category.slice(0, 5).map((c) => (
                <li key={c.key}>
                  <span>{label(c.key)}</span>
                  <Money value={c.amount} />
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {data.daily.length > 1 ? (
          <>
            <h3 className={s.subhead}>{t("daily")}</h3>
            <ul className={s.dailyBars}>
              {data.daily.map((d) => (
                <li key={d.day}>
                  <span className={s.dailyDay}>
                    {formatDate(`${d.day}T12:00:00Z`, locale, "UTC")}
                  </span>
                  <span className={s.dailyTrack} aria-hidden="true">
                    <span
                      className={s.dailyFill}
                      style={{ inlineSize: `${(Number(d.amount.amount) / max) * 100}%` }}
                    />
                  </span>
                  <Money value={d.amount} />
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p className={s.note}>
          {t("open", {
            expenses: data.pending_expenses,
            payments: data.pending_settlements,
            confirmed: formatMoney(data.confirmed_settlements, locale),
          })}
        </p>
      </div>
    </section>
  );
}
