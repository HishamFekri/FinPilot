"use client";

import { ChevronDown, Plus, Search, SlidersHorizontal } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";
import type { Locale } from "@/i18n/routing";
import { financeApi, type HistoryFilters, type Transaction } from "@/lib/finance";
import { localeTag } from "@/lib/format";
import { cn } from "@/lib/utils";
import { todayIn } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { Button, Notice, Spinner } from "../ui";
import { FinanceGate, useFinance } from "./finance-provider";
import s from "./finance.module.css";
import { TransactionRow, useAccountName, useCategoryName, useFinanceError } from "./parts";
import { TransactionDialog, useTxnDialog } from "./transaction-dialog";
import { Select } from "../select";

const TYPES = ["expense", "income", "transfer", "adjustment", "opening_balance"] as const;
const PAGE = 30;

export function TransactionsView() {
  return (
    <FinanceGate>
      <History />
    </FinanceGate>
  );
}

/** A page of results, tagged with the request it answers: an older answer is never shown. */
type List = {
  for: string;
  kind: "ready" | "failed";
  items: Transaction[];
  next: string | null;
  moreFailed: string | null;
};

function History() {
  const t = useTranslations("app.transactions");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const { accounts, categories, revision, quickAdd } = useFinance();
  const message = useFinanceError();
  const categoryName = useCategoryName();
  const accountName = useAccountName();
  const id = useId();

  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState<HistoryFilters["type"] | "">("");
  // A link from Analytics arrives with its filters in the address.
  const linked = useSearchParams();
  const [categoryId, setCategoryId] = useState(linked.get("category") ?? "");
  const [accountId, setAccountId] = useState("");
  const [from, setFrom] = useState(linked.get("from") ?? "");
  const [to, setTo] = useState(linked.get("to") ?? "");
  const [currency] = useState(linked.get("currency") ?? "");
  const [voided, setVoided] = useState(false);
  // Phones only: the filters other than search start folded so the entries come first.
  const [moreOpen, setMoreOpen] = useState(() => linked.has("from") || linked.has("category"));
  const [list, setList] = useState<List>({
    for: "",
    kind: "ready",
    items: [],
    next: null,
    moreFailed: null,
  });
  const [loadingMore, setLoadingMore] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const dialog = useTxnDialog();

  // Typing settles before it searches.
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const filters = useMemo<HistoryFilters>(
    () => ({
      type: type || undefined,
      category_id: categoryId || undefined,
      account_id: accountId || undefined,
      from: from || undefined,
      to: to || undefined,
      currency: currency || undefined,
      q: q || undefined,
      include_voided: voided || undefined,
    }),
    [type, categoryId, accountId, from, to, currency, q, voided],
  );
  const filtered = Object.values(filters).some(Boolean);
  // A new filter, a save anywhere (revision), or a retry: each is a new request.
  const request = `${JSON.stringify(filters)}|${revision}|${attempt}`;
  const loading = list.for !== request;

  useEffect(() => {
    let cancelled = false;
    financeApi
      .history(filters, null, PAGE)
      .then((page) => {
        if (cancelled) return;
        setList({
          for: request,
          kind: "ready",
          items: page.items,
          next: page.next_cursor,
          moreFailed: null,
        });
      })
      .catch((error: unknown) => {
        if (cancelled || handleAuthError(error)) return;
        setList({ for: request, kind: "failed", items: [], next: null, moreFailed: null });
      });
    return () => {
      cancelled = true;
    };
  }, [filters, request, handleAuthError]);

  const loadMore = async () => {
    if (!list.next) return;
    setLoadingMore(true);
    try {
      const page = await financeApi.history(filters, list.next, PAGE);
      setList((prev) =>
        prev.for !== request
          ? prev
          : {
              ...prev,
              items: [
                ...prev.items,
                ...page.items.filter((i) => !prev.items.some((p) => p.id === i.id)),
              ],
              next: page.next_cursor,
              moreFailed: null,
            },
      );
    } catch (error) {
      if (!handleAuthError(error)) {
        const text = message(error);
        setList((prev) => ({ ...prev, moreFailed: text }));
      }
    } finally {
      setLoadingMore(false);
    }
  };

  const clear = () => {
    setSearch("");
    setQ("");
    setType("");
    setCategoryId("");
    setAccountId("");
    setFrom("");
    setTo("");
    setVoided(false);
  };

  // Grouped by the day each entry belongs to.
  const today = todayIn(user.time_zone);
  const yesterday = useMemo(() => {
    const d = new Date(`${today}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() - 1);
    return d.toISOString().slice(0, 10);
  }, [today]);
  const dayLabel = (date: string) =>
    date === today
      ? t("today")
      : date === yesterday
        ? t("yesterday")
        : new Intl.DateTimeFormat(localeTag(locale), {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: date.slice(0, 4) === today.slice(0, 4) ? undefined : "numeric",
            timeZone: "UTC",
          }).format(new Date(`${date}T12:00:00Z`));
  const groups: { date: string; items: Transaction[] }[] = [];
  for (const item of list.items) {
    const last = groups.at(-1);
    if (last?.date === item.effective_date) last.items.push(item);
    else groups.push({ date: item.effective_date, items: [item] });
  }

  const pickable = categories.filter((c) => !type || c.kind === type);
  const moreCount = [type, categoryId, accountId, from || to].filter(Boolean).length;

  return (
    <>
      <div className={s.historyHead}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <Button variant="primary" onClick={() => quickAdd.setOpen(true)}>
          <Plus aria-hidden="true" />
          {t("empty.cta")}
        </Button>
      </div>

      <div
        role="search"
        id={`${id}-filters`}
        className={s.filters}
        data-folded={moreOpen ? undefined : true}
        aria-label={t("filters.label")}
      >
        <div className={appStyles.field}>
          <label className={s.compactLabel} htmlFor={`${id}-q`}>
            {t("filters.search")}
          </label>
          <div className={s.searchWrap}>
            <Search aria-hidden="true" />
            <input
              id={`${id}-q`}
              type="search"
              className={appStyles.input}
              dir="auto"
              maxLength={100}
              placeholder={t("filters.searchPlaceholder")}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </div>
        <div className={appStyles.field} data-more>
          <label className={s.compactLabel} htmlFor={`${id}-type`}>
            {t("filters.type")}
          </label>
          <Select
            id={`${id}-type`}
            value={type}
            onValueChange={(value) => {
              const next = value as HistoryFilters["type"] | "";
              setType(next);
              const chosen = categories.find((c) => c.id === categoryId);
              if (chosen && next && chosen.kind !== next) setCategoryId("");
            }}
          >
            <option value="">{t("filters.allTypes")}</option>
            {TYPES.map((value) => (
              <option key={value} value={value}>
                {t(`types.${value}`)}
              </option>
            ))}
          </Select>
        </div>
        <div className={appStyles.field} data-more>
          <label className={s.compactLabel} htmlFor={`${id}-category`}>
            {t("filters.category")}
          </label>
          <Select id={`${id}-category`} value={categoryId} onValueChange={setCategoryId}>
            <option value="">{t("filters.allCategories")}</option>
            {pickable.map((c) => (
              <option key={c.id} value={c.id}>
                {categoryName(c)}
              </option>
            ))}
          </Select>
        </div>
        <div className={appStyles.field} data-more>
          <label className={s.compactLabel} htmlFor={`${id}-account`}>
            {t("filters.account")}
          </label>
          <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
            <option value="">{t("filters.allAccounts")}</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {`${accountName(a)} · ${a.currency}`}
              </option>
            ))}
          </Select>
        </div>
        <fieldset className={s.filterDates} data-more>
          <legend className={appStyles.srOnly}>{t("filters.dates")}</legend>
          <div className={appStyles.field}>
            <label className={s.compactLabel} htmlFor={`${id}-from`}>
              {t("filters.from")}
            </label>
            <input
              id={`${id}-from`}
              type="date"
              className={appStyles.input}
              value={from}
              max={to || today}
              onChange={(event) => setFrom(event.target.value)}
            />
          </div>
          <div className={appStyles.field}>
            <label className={s.compactLabel} htmlFor={`${id}-to`}>
              {t("filters.to")}
            </label>
            <input
              id={`${id}-to`}
              type="date"
              className={appStyles.input}
              value={to}
              min={from || undefined}
              max={today}
              onChange={(event) => setTo(event.target.value)}
            />
          </div>
        </fieldset>
      </div>
      <div className={s.filterFoot}>
        <button
          type="button"
          className={s.moreFilters}
          aria-expanded={moreOpen}
          aria-controls={`${id}-filters`}
          onClick={() => setMoreOpen((v) => !v)}
        >
          <SlidersHorizontal aria-hidden="true" />
          {moreCount > 0 ? t("filters.moreCount", { count: moreCount }) : t("filters.more")}
          <ChevronDown aria-hidden="true" data-open={moreOpen || undefined} />
        </button>
        <label className={s.checkRow}>
          <input
            type="checkbox"
            checked={voided}
            onChange={(event) => setVoided(event.target.checked)}
          />
          <span>{t("filters.showVoided")}</span>
        </label>
        {filtered ? (
          <button type="button" className={appStyles.linkBtn} onClick={clear}>
            {t("filters.clear")}
          </button>
        ) : null}
      </div>

      <section className={s.panel} aria-labelledby="history-title" aria-busy={loading || undefined}>
        <h2 id="history-title" className={appStyles.srOnly}>
          {t("title")}
        </h2>
        {list.kind === "failed" ? (
          <div className={s.panelBody}>
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
          </div>
        ) : loading && list.items.length === 0 ? (
          <p className={cn(appStyles.muted, s.panelBody)} role="status">
            <Spinner /> <span>{t("loading")}</span>
          </p>
        ) : list.items.length === 0 ? (
          <div className={s.emptyState}>
            {filtered ? (
              <>
                <p className={s.emptyTitle}>{t("noMatches")}</p>
                <Button size="sm" onClick={clear}>
                  {t("filters.clear")}
                </Button>
              </>
            ) : (
              <>
                <svg className={s.emptyArt} viewBox="0 0 112 44" aria-hidden="true">
                  <path d="M4 34 C 26 34, 30 10, 56 12 S 92 34, 108 10" />
                </svg>
                <p className={s.emptyTitle}>{t("empty.title")}</p>
                <p className={cn(appStyles.muted, appStyles.small)}>{t("empty.body")}</p>
                <Button variant="primary" size="sm" onClick={() => quickAdd.setOpen(true)}>
                  {t("empty.cta")}
                </Button>
              </>
            )}
          </div>
        ) : (
          <div className={cn(loading && s.refreshing)}>
            {groups.map((group) => (
              <section key={group.date} aria-label={dayLabel(group.date)}>
                <h3 className={s.day}>
                  <bdi>{dayLabel(group.date)}</bdi>
                </h3>
                <ul className={s.rows}>
                  {group.items.map((txn) => (
                    <li key={txn.id}>
                      <TransactionRow txn={txn} onOpen={(item) => dialog.show(item.id)} />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <div className={s.listFoot}>
              <span className={cn(appStyles.muted, appStyles.small)} aria-live="polite">
                {t("count", { count: list.items.length })}
              </span>
              {list.next ? (
                <Button size="sm" busy={loadingMore} onClick={() => void loadMore()}>
                  {t("loadMore")}
                </Button>
              ) : null}
            </div>
            {list.moreFailed ? (
              <div className={s.panelBody}>
                <Notice tone="error">{list.moreFailed}</Notice>
              </div>
            ) : null}
          </div>
        )}
      </section>

      <TransactionDialog dialog={dialog} />
    </>
  );
}
