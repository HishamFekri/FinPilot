"use client";

import { ArrowLeftRight, Plus, SlidersHorizontal } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { Link } from "@/i18n/navigation";
import { type Account, accountsApi, type CurrencyInfo, type CurrencyTotal } from "@/lib/accounts";
import { cn } from "@/lib/utils";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { Button, Notice, Spinner, Tag } from "../ui";
import { useFinanceRevision } from "../finance/finance-provider";
import { CreateAccountDialog } from "./account-form";
import { useAccountName } from "../finance/parts";
import s from "./accounts.module.css";
import { AdjustDialog } from "./adjust-dialog";
import { Money, TYPE_ICONS } from "./parts";
import type { Money as MoneyValue } from "@/lib/money";
import { useDisplayCurrency } from "../balances/display-currency";
import { TotalBalanceCard, useTotalBalance } from "../balances/total-balance";
import { TransferDialog } from "./transfer-dialog";

type Data = { active: Account[]; totals: CurrencyTotal[]; currencies: CurrencyInfo[] };
type Load = { kind: "loading" } | { kind: "ready"; data: Data } | { kind: "failed" };

export function useAccountData() {
  const { handleAuthError } = useAuth();
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  const fetchAll = useCallback(async (): Promise<Load | null> => {
    try {
      const [active, totals, currencies] = await Promise.all([
        accountsApi.list("active"),
        accountsApi.totals(),
        accountsApi.currencies(),
      ]);
      return { kind: "ready", data: { active, totals, currencies } };
    } catch (error) {
      return handleAuthError(error) ? null : { kind: "failed" };
    }
  }, [handleAuthError]);

  const reload = useCallback(async () => {
    const result = await fetchAll();
    if (result) setLoad(result);
  }, [fetchAll]);

  // Refetches after anything is saved elsewhere (Quick Add in the top bar, for one).
  const revision = useFinanceRevision();
  useEffect(() => {
    let cancelled = false;
    void fetchAll().then((result) => {
      if (!cancelled && result) setLoad(result);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchAll, revision]);

  return { load, reload };
}

export function AccountCard({
  account,
  equivalent,
}: {
  account: Account;
  /** About how much it is in the chosen display currency (null: no rate; absent: same). */
  equivalent?: MoneyValue | null;
}) {
  const t = useTranslations("app.accounts");
  const types = useTranslations("app.accounts.types");
  const accountName = useAccountName();
  const Icon = TYPE_ICONS[account.type];
  return (
    <Link
      href={`/app/accounts/${account.id}`}
      className={cn(s.card, account.status === "archived" && s.archivedCard)}
    >
      <span className={s.cardTop}>
        <span className={s.typeIcon}>
          <Icon aria-hidden="true" />
        </span>
        <span className={s.cardName}>
          <span className={s.cardTitle} dir="auto">
            {accountName(account)}
          </span>
          <span className={s.cardMeta}>
            {types(account.type)}
            {" · "}
            <bdi dir="ltr">{account.currency}</bdi>
          </span>
        </span>
        {account.is_default ? <Tag tone="accent">{t("defaultTag")}</Tag> : null}
      </span>
      <span className={s.cardBalance}>
        <Money value={account.balance} className={s.balance} />
        {equivalent !== undefined && equivalent?.currency !== account.currency ? (
          <span className={s.cardEquivalent}>
            {equivalent ? (
              <>
                {"≈ "}
                <Money value={equivalent} />
              </>
            ) : (
              t("noRate")
            )}
          </span>
        ) : null}
      </span>
    </Link>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  const t = useTranslations("app.accounts.empty");
  return (
    <section className={s.empty} aria-labelledby="accounts-empty">
      <svg className={s.emptyArt} viewBox="0 0 132 56" aria-hidden="true">
        <path d="M8 44 C 36 44, 40 14, 66 20 S 104 46, 124 12" />
        <circle cx="8" cy="44" r="5" fill="#8fd0ff" />
        <circle cx="124" cy="12" r="5" fill="currentColor" />
      </svg>
      <h2 id="accounts-empty" className={s.emptyTitle}>
        {t("title")}
      </h2>
      <p className={appStyles.muted}>{t("body")}</p>
      <Button variant="primary" onClick={onAdd}>
        <Plus aria-hidden="true" />
        {t("cta")}
      </Button>
    </section>
  );
}

export function AccountsView() {
  const t = useTranslations("app.accounts");
  const [target, setTarget] = useDisplayCurrency();
  const total = useTotalBalance(target);
  const equivalentOf = (id: string) => {
    const found = total.data?.accounts.find((a) => a.id === id);
    return found ? found.equivalent : undefined;
  };
  const { handleAuthError } = useAuth();
  const { load, reload } = useAccountData();
  const [dialog, setDialog] = useState<"create" | "transfer" | "adjust" | null>(null);
  const [archived, setArchived] = useState<Account[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [archivedFailed, setArchivedFailed] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const toggleArchived = async () => {
    const next = !showArchived;
    setShowArchived(next);
    if (!next) return;
    setArchivedFailed(false);
    try {
      setArchived(await accountsApi.list("archived"));
    } catch (error) {
      if (!handleAuthError(error)) setArchivedFailed(true);
    }
  };

  const finished = (key: string) => {
    setDone(t(`done.${key as "created"}`));
    void reload();
  };

  if (load.kind === "loading") {
    return (
      <p className={appStyles.muted} role="status">
        <Spinner /> <span>{t("loading")}</span>
      </p>
    );
  }
  if (load.kind === "failed") {
    return (
      <Notice tone="error">
        {t("loadFailed")}{" "}
        <button type="button" className={appStyles.linkBtn} onClick={() => void reload()}>
          {t("retry")}
        </button>
      </Notice>
    );
  }

  const { active, totals, currencies } = load.data;

  return (
    <>
      <header className={s.head}>
        <div className={s.headText}>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        {active.length > 0 ? (
          <div className={s.actions}>
            <Button variant="primary" onClick={() => setDialog("create")}>
              <Plus aria-hidden="true" />
              {t("actions.add")}
            </Button>
            <Button
              onClick={() => setDialog("transfer")}
              disabled={active.length < 2}
              title={active.length < 2 ? t("transfer.needTwo") : undefined}
            >
              <ArrowLeftRight aria-hidden="true" />
              {t("actions.transfer")}
            </Button>
            <Button onClick={() => setDialog("adjust")}>
              <SlidersHorizontal aria-hidden="true" />
              {t("actions.adjust")}
            </Button>
          </div>
        ) : null}
      </header>

      {done ? (
        <Notice tone="success" className={appStyles.pageNotice}>
          {done}
        </Notice>
      ) : null}

      {active.length === 0 ? (
        <EmptyState onAdd={() => setDialog("create")} />
      ) : (
        <>
          <div className={s.summaryPair}>
            <TotalBalanceCard
              target={target}
              onTarget={setTarget}
              data={total.data}
              failed={Boolean(total.error)}
              loading={total.loading}
              onRefresh={total.refresh}
              accountCount={active.length}
            />
          </div>
          <section className={s.overview} aria-labelledby="overview-title">
            <div className={s.sectionHead}>
              <h2 id="overview-title" className={s.sectionTitle}>
                {t("overview.title")}
              </h2>
            </div>
            <ul className={s.totals}>
              {totals.map((total) => (
                <li key={total.balance.currency} className={s.total}>
                  <span className={s.totalLabel}>
                    <bdi dir="ltr" className={s.totalCode}>
                      {total.balance.currency}
                    </bdi>
                    <span>{t("overview.accounts", { count: total.accounts })}</span>
                  </span>
                  <Money value={total.balance} className={s.totalAmount} />
                </li>
              ))}
            </ul>
            {totals.length > 1 ? (
              <p className={cn(s.sectionNote, s.overviewNote)}>{t("overview.separate")}</p>
            ) : null}
          </section>

          <section aria-labelledby="accounts-title">
            <div className={s.sectionHead}>
              <h2 id="accounts-title" className={s.sectionTitle}>
                {t("listTitle")}
              </h2>
            </div>
            <ul className={s.cards}>
              {active.map((account) => (
                <li key={account.id}>
                  <AccountCard account={account} equivalent={equivalentOf(account.id)} />
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      <div className={s.toggleRow}>
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={showArchived}
          onClick={() => void toggleArchived()}
        >
          {showArchived ? t("actions.hideArchived") : t("actions.showArchived")}
        </Button>
      </div>
      {showArchived ? (
        <section aria-labelledby="archived-title">
          <div className={s.sectionHead}>
            <h2 id="archived-title" className={s.sectionTitle}>
              {t("archivedTitle")}
            </h2>
          </div>
          {archivedFailed ? (
            <Notice tone="error">{t("loadFailed")}</Notice>
          ) : archived === null ? (
            <p className={appStyles.muted} role="status">
              <Spinner /> <span>{t("loading")}</span>
            </p>
          ) : archived.length === 0 ? (
            <p className={appStyles.muted}>{t("archivedEmpty")}</p>
          ) : (
            <ul className={s.cards}>
              {archived.map((account) => (
                <li key={account.id}>
                  <AccountCard account={account} />
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <CreateAccountDialog
        open={dialog === "create"}
        onOpenChange={(open) => setDialog(open ? "create" : null)}
        currencies={currencies}
        suggestedCurrency={totals[0]?.balance.currency}
        onCreated={() => finished("created")}
      />
      <TransferDialog
        accounts={active}
        currencies={currencies}
        open={dialog === "transfer"}
        onOpenChange={(open) => setDialog(open ? "transfer" : null)}
        onTransferred={() => finished("transferred")}
      />
      <AdjustDialog
        accounts={active}
        currencies={currencies}
        open={dialog === "adjust"}
        onOpenChange={(open) => setDialog(open ? "adjust" : null)}
        onAdjusted={() => finished("adjusted")}
      />
    </>
  );
}
