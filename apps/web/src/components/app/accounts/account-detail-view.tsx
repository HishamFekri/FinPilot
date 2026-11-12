"use client";

import { AlertDialog } from "@base-ui/react/alert-dialog";
import {
  Archive,
  ArchiveRestore,
  ArrowDownLeft,
  ArrowLeft,
  ArrowLeftRight,
  ArrowUpRight,
  HandCoins,
  Pencil,
  PlaneLanding,
  PlaneTakeoff,
  SlidersHorizontal,
  Sparkles,
  Star,
  Trash2,
  Undo2,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { Link, useRouter } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { ApiError } from "@/lib/api";
import { type Account, type ActivityItem, accountsApi } from "@/lib/accounts";
import { formatDate } from "@/lib/format";
import { isNegative, isZero } from "@/lib/money";
import { cn } from "@/lib/utils";
import appStyles from "../app.module.css";
import { usePortalRoot } from "../app-shell";
import { useAuth } from "../auth-provider";
import { bdi, Button, Notice, Spinner, Tag } from "../ui";
import { EditAccountDialog } from "./account-form";
import { useFinanceRevision, useFinanceState } from "../finance/finance-provider";
import { EquivalentsDialog, EquivalentsStrip } from "../finance/equivalents";
import { useAccountName } from "../finance/parts";
import s from "./accounts.module.css";
import { useAccountData } from "./accounts-view";
import { AdjustDialog } from "./adjust-dialog";
import { Money, TYPE_ICONS, useErrorMessage } from "./parts";
import { TransferDialog } from "./transfer-dialog";
import { ConfirmDialog } from "../confirm-dialog";
import { DeleteOrArchive, useRemovalFailure } from "../removal";

type Detail =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "ready"; account: Account; items: ActivityItem[]; hasMore: boolean };

const ACTIVITY_ICONS: Record<ActivityItem["type"], typeof Sparkles> = {
  opening_balance: Sparkles,
  adjustment: SlidersHorizontal,
  transfer: ArrowLeftRight,
  income: ArrowDownLeft,
  expense: ArrowUpRight,
  debt_start: HandCoins,
  debt_payment: HandCoins,
  trip_expense_payment: PlaneTakeoff,
  trip_settlement_paid: PlaneTakeoff,
  trip_settlement_received: PlaneLanding,
  refund: Undo2,
};

export function AccountDetailView({ accountId }: { accountId: string }) {
  const t = useTranslations("app.accounts");
  const types = useTranslations("app.accounts.types");
  const kinds = useTranslations("app.transactions.types");
  const accountName = useAccountName();
  const locale = useLocale() as Locale;
  const root = usePortalRoot();
  const { handleAuthError } = useAuth();
  const message = useErrorMessage();
  const { load: listLoad, reload: reloadList } = useAccountData();
  const [detail, setDetail] = useState<Detail>({ kind: "loading" });
  const [dialog, setDialog] = useState<"edit" | "adjust" | "transfer" | "archive" | null>(null);
  const [deleting, setDeleting] = useState(false);
  const removalFailure = useRemovalFailure();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [equivalentOpen, setEquivalentOpen] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const fetchDetail = useCallback(async (): Promise<Detail | null> => {
    try {
      const [account, page] = await Promise.all([
        accountsApi.get(accountId),
        accountsApi.activity(accountId),
      ]);
      return { kind: "ready", account, items: page.items, hasMore: page.has_more };
    } catch (error) {
      if (handleAuthError(error)) return null;
      if (error instanceof ApiError && error.kind === "not_found") return { kind: "missing" };
      return { kind: "failed" };
    }
  }, [accountId, handleAuthError]);

  const refresh = useCallback(async () => {
    const result = await fetchDetail();
    if (result) setDetail(result);
    await reloadList();
  }, [fetchDetail, reloadList]);

  const revision = useFinanceRevision();
  const finance = useFinanceState();
  // Refetches after anything is saved elsewhere (Quick Add in the top bar, for one).
  useEffect(() => {
    let cancelled = false;
    void fetchDetail().then((result) => {
      if (!cancelled && result) setDetail(result);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchDetail, revision]);

  const back = (
    <Link href="/app/accounts" className={s.back}>
      <ArrowLeft aria-hidden="true" />
      {t("actions.back")}
    </Link>
  );

  if (detail.kind === "loading") {
    return (
      <>
        {back}
        <p className={appStyles.muted} role="status">
          <Spinner /> <span>{t("loading")}</span>
        </p>
      </>
    );
  }
  if (detail.kind !== "ready") {
    return (
      <>
        {back}
        <Notice tone="error">{detail.kind === "missing" ? t("missing") : t("loadFailed")}</Notice>
      </>
    );
  }

  const { account, items, hasMore } = detail;
  const Icon = TYPE_ICONS[account.type];
  const archived = account.status === "archived";
  const others = listLoad.kind === "ready" ? listLoad.data.active : [];
  const currencies = listLoad.kind === "ready" ? listLoad.data.currencies : [];
  const names = new Map([...others, account].map((a) => [a.id, accountName(a)] as const));

  const makeDefault = async () => {
    setBusy(true);
    setNotice(null);
    try {
      await accountsApi.makeDefault(account.id);
      setNotice({ tone: "success", text: t("done.madeDefault") });
      await finance?.changed();
    } catch (error) {
      if (handleAuthError(error)) return;
      setNotice({ tone: "error", text: message(error) });
    } finally {
      setBusy(false);
    }
  };

  const changeStatus = async () => {
    setBusy(true);
    setNotice(null);
    try {
      if (archived) await accountsApi.unarchive(account.id);
      else await accountsApi.archive(account.id);
      setDialog(null);
      setNotice({ tone: "success", text: t(archived ? "done.restored" : "done.archived") });
      await refresh();
    } catch (error) {
      if (handleAuthError(error)) return;
      setDialog(null);
      setNotice({ tone: "error", text: message(error) });
    } finally {
      setBusy(false);
    }
  };

  const describe = (item: ActivityItem) => {
    if (item.type === "transfer") {
      const other = item.other_account_id ? names.get(item.other_account_id) : undefined;
      const incoming = !item.amount.amount.startsWith("-");
      return t.rich(incoming ? "activity.transferFrom" : "activity.transferTo", {
        name: other ?? t("activity.anotherAccount"),
        bdi,
      });
    }
    if (item.type === "opening_balance") return t("activity.opening");
    if (item.type === "adjustment") return t("activity.adjustment");
    return item.description ?? kinds(item.type);
  };

  return (
    <>
      {back}
      {notice ? (
        <Notice tone={notice.tone} className={appStyles.pageNotice}>
          {notice.text}
        </Notice>
      ) : null}

      <section className={s.hero} aria-labelledby="account-title">
        <div className={s.heroTop}>
          <div className={s.heroName}>
            <span className={s.typeIcon}>
              <Icon aria-hidden="true" />
            </span>
            <div className={s.cardName}>
              <h1 id="account-title" className={s.heroTitle} dir="auto">
                {accountName(account)}
              </h1>
              <span className={s.cardMeta}>
                {types(account.type)}
                {" · "}
                <bdi dir="ltr">{account.currency}</bdi>
              </span>
            </div>
          </div>
          {archived ? <Tag>{t("status.archived")}</Tag> : null}
          {account.is_default ? <Tag tone="accent">{t("defaultTag")}</Tag> : null}
        </div>
        <div>
          <span className={appStyles.hint}>{t("balance")}</span>
          <div>
            <Money value={account.balance} className={s.heroBalance} />
          </div>
          {isZero(account.balance) || isNegative(account.balance) ? null : (
            <EquivalentsStrip amount={account.balance} onMore={() => setEquivalentOpen(true)} />
          )}
          <EquivalentsDialog
            open={equivalentOpen}
            onOpenChange={setEquivalentOpen}
            initial={account.balance}
          />
        </div>
        <dl className={s.facts}>
          <div>
            <dt>{t("openedOn")}</dt>
            <dd>
              <bdi>{formatDate(account.opened_on, locale, "UTC")}</bdi>
            </dd>
          </div>
          {account.description ? (
            <div>
              <dt>{t("form.description")}</dt>
              <dd dir="auto">{account.description}</dd>
            </div>
          ) : null}
        </dl>
        <div className={s.actions}>
          {archived ? null : (
            <>
              <Button size="sm" onClick={() => setDialog("transfer")} disabled={others.length < 2}>
                <ArrowLeftRight aria-hidden="true" />
                {t("actions.transfer")}
              </Button>
              <Button size="sm" onClick={() => setDialog("adjust")}>
                <SlidersHorizontal aria-hidden="true" />
                {t("actions.adjust")}
              </Button>
            </>
          )}
          {archived || account.is_default ? null : (
            <Button size="sm" onClick={() => void makeDefault()} disabled={busy}>
              <Star aria-hidden="true" />
              {t("actions.makeDefault")}
            </Button>
          )}
          <Button size="sm" onClick={() => setDialog("edit")}>
            <Pencil aria-hidden="true" />
            {t("actions.edit")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDialog("archive")}>
            {archived ? <ArchiveRestore aria-hidden="true" /> : <Archive aria-hidden="true" />}
            {archived ? t("actions.unarchive") : t("actions.archive")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDeleting(true)}>
            <Trash2 aria-hidden="true" />
            {t("actions.delete")}
          </Button>
        </div>
      </section>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t.rich("deleteDialog.title", { name: accountName(account), bdi })}
        body={
          <>
            <p>{t("deleteDialog.body")}</p>
            <DeleteOrArchive />
          </>
        }
        confirm={t("actions.delete")}
        onConfirm={async () => {
          try {
            await accountsApi.remove(account.id);
            router.push("/app/accounts");
            return null;
          } catch (error) {
            return removalFailure(error);
          }
        }}
      />

      <section className={appStyles.panel} aria-labelledby="activity-title">
        <div className={appStyles.panelHead}>
          <h2 id="activity-title" className={appStyles.panelTitle}>
            {t("activity.title")}
          </h2>
        </div>
        {items.length === 0 ? (
          <p className={cn(appStyles.panelBody, appStyles.muted)}>{t("activity.empty")}</p>
        ) : (
          <ul className={s.activity}>
            {items.map((item) => {
              const ItemIcon = ACTIVITY_ICONS[item.type] ?? Sparkles;
              const described =
                item.type !== "transfer" &&
                item.type !== "opening_balance" &&
                item.type !== "adjustment";
              const extra =
                described && item.description ? kinds(item.type) : (item.notes ?? item.description);
              return (
                <li key={item.id} className={s.activityRow}>
                  <span className={s.typeIcon}>
                    <ItemIcon aria-hidden="true" />
                  </span>
                  <span className={s.activityText}>
                    <span className={s.activityTitle}>{describe(item)}</span>
                    <span className={s.activityMeta}>
                      <bdi>{formatDate(item.effective_date, locale, "UTC")}</bdi>
                      {extra ? (
                        <>
                          {" · "}
                          <bdi dir="auto">{extra}</bdi>
                        </>
                      ) : null}
                    </span>
                  </span>
                  <Money
                    value={item.amount}
                    signed
                    className={cn(
                      s.activityAmount,
                      !item.amount.amount.startsWith("-") && s.positive,
                    )}
                  />
                </li>
              );
            })}
          </ul>
        )}
        {hasMore ? (
          <p className={cn(appStyles.panelFoot, appStyles.hint)}>
            {t("activity.latest", { count: items.length })}
          </p>
        ) : null}
      </section>

      <EditAccountDialog
        account={account}
        open={dialog === "edit"}
        onOpenChange={(open) => setDialog(open ? "edit" : null)}
        onSaved={() => {
          setNotice({ tone: "success", text: t("done.saved") });
          void refresh();
        }}
      />
      {currencies.length > 0 ? (
        <>
          <AdjustDialog
            accounts={[account]}
            initialAccountId={account.id}
            currencies={currencies}
            open={dialog === "adjust"}
            onOpenChange={(open) => setDialog(open ? "adjust" : null)}
            onAdjusted={() => {
              setNotice({ tone: "success", text: t("done.adjusted") });
              void refresh();
            }}
          />
          <TransferDialog
            accounts={others}
            initialFromId={account.id}
            currencies={currencies}
            open={dialog === "transfer"}
            onOpenChange={(open) => setDialog(open ? "transfer" : null)}
            onTransferred={() => {
              setNotice({ tone: "success", text: t("done.transferred") });
              void refresh();
            }}
          />
        </>
      ) : null}

      <AlertDialog.Root
        open={dialog === "archive"}
        onOpenChange={(open) => setDialog(open ? "archive" : null)}
      >
        <AlertDialog.Portal container={root}>
          <AlertDialog.Backdrop className={appStyles.dialogBackdrop} />
          <AlertDialog.Popup className={appStyles.dialog}>
            {archived ? (
              <>
                <AlertDialog.Title className={appStyles.dialogTitle}>
                  {t.rich("archive.restoreTitle", { name: accountName(account), bdi })}
                </AlertDialog.Title>
                <AlertDialog.Description className={appStyles.dialogBody}>
                  {t("archive.restoreBody")}
                </AlertDialog.Description>
                <div className={appStyles.dialogActions}>
                  <AlertDialog.Close className={`${appStyles.btn} ${appStyles.btnSecondary}`}>
                    {t("form.cancel")}
                  </AlertDialog.Close>
                  <Button variant="primary" busy={busy} onClick={() => void changeStatus()}>
                    {t("actions.unarchive")}
                  </Button>
                </div>
              </>
            ) : isZero(account.balance) ? (
              <>
                <AlertDialog.Title className={appStyles.dialogTitle}>
                  {t.rich("archive.title", { name: accountName(account), bdi })}
                </AlertDialog.Title>
                <AlertDialog.Description className={appStyles.dialogBody}>
                  {t("archive.body")}
                </AlertDialog.Description>
                <div className={appStyles.dialogActions}>
                  <AlertDialog.Close className={`${appStyles.btn} ${appStyles.btnSecondary}`}>
                    {t("form.cancel")}
                  </AlertDialog.Close>
                  <Button variant="danger" busy={busy} onClick={() => void changeStatus()}>
                    {t("actions.archive")}
                  </Button>
                </div>
              </>
            ) : (
              <>
                {/* Only an account at zero can be archived (FINANCIAL_RULES.md §4.9). Say why,
                    and offer the two ways to get there. */}
                <AlertDialog.Title className={appStyles.dialogTitle}>
                  {t.rich("archive.nonZeroTitle", { name: accountName(account), bdi })}
                </AlertDialog.Title>
                <AlertDialog.Description className={appStyles.dialogBody}>
                  {t("archive.nonZeroBody")}
                </AlertDialog.Description>
                <div className={appStyles.dialogActions}>
                  <AlertDialog.Close className={`${appStyles.btn} ${appStyles.btnGhost}`}>
                    {t("form.cancel")}
                  </AlertDialog.Close>
                  <Button onClick={() => setDialog("adjust")}>{t("archive.adjustZero")}</Button>
                  <Button
                    variant="primary"
                    onClick={() => setDialog("transfer")}
                    disabled={others.length < 2}
                  >
                    {t("archive.moveMoney")}
                  </Button>
                </div>
              </>
            )}
          </AlertDialog.Popup>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </>
  );
}
