"use client";

import { Archive, ArchiveRestore, Pencil, Plus, Star, Trash2, Zap } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey } from "@/lib/accounts";
import { type Template, templatesApi } from "@/lib/everyday";
import { editableAmount, formatMoney, parseAmount } from "@/lib/money";
import { cn } from "@/lib/utils";
import { AccountDialog } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { MoneyInput } from "../money-input";
import { Button, Loading, Notice } from "../ui";
import { FinanceGate, useFinance } from "./finance-provider";
import s from "./finance.module.css";
import { CategoryIcon, useAccountName, useCategoryName, useFinanceError } from "./parts";
import { Select } from "../select";
import { useConfirm } from "../confirm-dialog";

/**
 * Quick templates are shortcuts, not schedules: choosing one opens Quick Add with its details and
 * today's date, and nothing is saved until the person presses Save there.
 */

function useTemplates(include = false) {
  const { handleAuthError } = useAuth();
  const { revision } = useFinance();
  const [items, setItems] = useState<{ for: string; list?: Template[]; failed?: boolean }>({
    for: "",
  });
  const [attempt, setAttempt] = useState(0);
  const stamp = `${revision}:${attempt}:${include}`;
  useEffect(() => {
    let cancelled = false;
    templatesApi
      .list(include)
      .then((list) => {
        if (!cancelled) setItems({ for: stamp, list });
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setItems({ for: stamp, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [stamp, include, handleAuthError]);
  return { ...items, reload: () => setAttempt((n) => n + 1) };
}

/** Favourite templates, next to Quick Add. One press opens Quick Add filled in. */
export function TemplateStrip({ limit = 6 }: { limit?: number }) {
  const t = useTranslations("app.templates");
  const locale = useLocale() as Locale;
  const { quickAdd, categories } = useFinance();
  const { list } = useTemplates();
  const favourites = (list ?? []).filter((x) => x.is_favourite).slice(0, limit);
  if (!list) return null;
  return (
    <div className={s.templateStrip} aria-label={t("favourites")} role="group">
      {favourites.map((x, i) => {
        const category = categories.find((c) => c.id === x.category_id);
        return (
          <button
            key={x.id}
            type="button"
            className={s.templateChip}
            onClick={() =>
              quickAdd.openWith({
                n: Date.now() + i,
                kind: x.kind,
                amount: x.amount?.amount ?? null,
                currency: x.amount?.currency ?? x.currency,
                accountId: x.account_id,
                categoryId: x.category_id,
                description: x.description ?? x.name,
                templateId: x.id,
              })
            }
            aria-label={t("use", {
              name: x.name,
              amount: x.amount ? formatMoney(x.amount, locale) : t("anyAmount"),
            })}
          >
            {category ? <CategoryIcon category={category} size="sm" /> : <Zap aria-hidden="true" />}
            <span className={s.templateName} dir="auto">
              {x.name}
            </span>
            {x.amount ? (
              <bdi dir="ltr" className={s.templateAmount}>
                {formatMoney(x.amount, locale)}
              </bdi>
            ) : null}
          </button>
        );
      })}
      <Link href="/app/settings/templates" className={s.templateManage}>
        {favourites.length ? t("manage") : t("emptyStrip")}
      </Link>
    </div>
  );
}

export function TemplatesView() {
  return (
    <FinanceGate>
      <TemplatesPage />
    </FinanceGate>
  );
}

function TemplatesPage() {
  const t = useTranslations("app.templates");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const { categories, quickAdd } = useFinance();
  const categoryName = useCategoryName();
  const accountName = useAccountName();
  const { accounts } = useFinance();
  const message = useFinanceError();
  const [showArchived, setShowArchived] = useState(false);
  const { list, failed, reload } = useTemplates(showArchived);
  const [form, setForm] = useState<{ n: number; template: Template | null } | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const act = async (job: () => Promise<unknown>, done: string) => {
    setNotice(null);
    try {
      await job();
      setNotice({ tone: "success", text: done });
      reload();
    } catch (error) {
      if (!handleAuthError(error)) setNotice({ tone: "error", text: message(error) });
    }
  };

  const [confirmEl, ask] = useConfirm();
  return (
    <div className={s.templatesPage}>
      {confirmEl}
      <div className={s.pageHeadRow}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
        <Button
          variant="primary"
          onClick={() => {
            setForm((p) => ({ n: (p?.n ?? 0) + 1, template: null }));
            setFormOpen(true);
          }}
        >
          <Plus aria-hidden="true" />
          {t("new")}
        </Button>
      </div>
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}
      <label className={s.inlineCheck}>
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        />
        <span>{t("showArchived")}</span>
      </label>
      {failed ? (
        <Notice tone="error">{message(null)}</Notice>
      ) : !list ? (
        <Loading />
      ) : list.length === 0 ? (
        <div className={s.emptyState}>
          <p className={s.emptyTitle}>{t("emptyTitle")}</p>
          <p className={cn(appStyles.muted, appStyles.small)}>{t("emptyBody")}</p>
        </div>
      ) : (
        <ul className={s.templateList}>
          {list.map((x) => {
            const category = categories.find((c) => c.id === x.category_id);
            const account = accounts.find((a) => a.id === x.account_id);
            return (
              <li key={x.id} className={cn(s.templateRow, x.status === "archived" && s.voided)}>
                {category ? <CategoryIcon category={category} /> : null}
                <span className={s.rowText}>
                  <span className={s.rowTitle} dir="auto">
                    {x.name}
                  </span>
                  <span className={s.rowMeta}>
                    <bdi>{category ? categoryName(category) : ""}</bdi>
                    {" · "}
                    <bdi>{account ? accountName(account) : t("defaultAccount")}</bdi>
                    {" · "}
                    {x.amount ? (
                      <bdi dir="ltr">{formatMoney(x.amount, locale)}</bdi>
                    ) : (
                      t("anyAmount")
                    )}
                  </span>
                </span>
                <span className={s.templateActions}>
                  <button
                    type="button"
                    className={s.iconAction}
                    aria-pressed={x.is_favourite}
                    aria-label={x.is_favourite ? t("unfavourite") : t("favourite")}
                    onClick={() =>
                      void act(
                        () =>
                          templatesApi.update(x.id, x.version, { is_favourite: !x.is_favourite }),
                        x.is_favourite ? t("unfavourited") : t("favourited"),
                      )
                    }
                  >
                    <Star aria-hidden="true" data-on={x.is_favourite || undefined} />
                  </button>
                  {x.status === "active" ? (
                    <Button
                      size="sm"
                      onClick={() =>
                        quickAdd.openWith({
                          n: Date.now(),
                          kind: x.kind,
                          amount: x.amount?.amount ?? null,
                          currency: x.amount?.currency ?? x.currency,
                          accountId: x.account_id,
                          categoryId: x.category_id,
                          description: x.description ?? x.name,
                          templateId: x.id,
                        })
                      }
                    >
                      {t("useShort")}
                    </Button>
                  ) : null}
                  <button
                    type="button"
                    className={s.iconAction}
                    aria-label={t("edit")}
                    onClick={() => {
                      setForm((p) => ({ n: (p?.n ?? 0) + 1, template: x }));
                      setFormOpen(true);
                    }}
                  >
                    <Pencil aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className={s.iconAction}
                    aria-label={x.status === "active" ? t("archive") : t("restore")}
                    onClick={() =>
                      void act(
                        () =>
                          templatesApi.update(x.id, x.version, {
                            status: x.status === "active" ? "archived" : "active",
                          }),
                        x.status === "active" ? t("archived") : t("restored"),
                      )
                    }
                  >
                    {x.status === "active" ? (
                      <Archive aria-hidden="true" />
                    ) : (
                      <ArchiveRestore aria-hidden="true" />
                    )}
                  </button>
                  <button
                    type="button"
                    className={s.iconAction}
                    aria-label={t("delete")}
                    onClick={async () => {
                      if (await ask({ title: t("deleteConfirm", { name: x.name }) }))
                        void act(() => templatesApi.remove(x.id), t("deleted"));
                    }}
                  >
                    <Trash2 aria-hidden="true" />
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <p className={s.note}>{t("rule")}</p>
      {form ? (
        <TemplateForm
          key={form.n}
          template={form.template}
          open={formOpen}
          onClose={() => setFormOpen(false)}
          onSaved={(text) => {
            setFormOpen(false);
            setNotice({ tone: "success", text });
            reload();
          }}
        />
      ) : null}
    </div>
  );
}

function TemplateForm({
  template,
  open,
  onClose,
  onSaved,
}: {
  template: Template | null;
  open: boolean;
  onClose: () => void;
  onSaved: (text: string) => void;
}) {
  const t = useTranslations("app.templates");
  const locale = useLocale() as Locale;
  const { handleAuthError } = useAuth();
  const { accounts, categories, currencies, defaultAccountId } = useFinance();
  const categoryName = useCategoryName();
  const accountName = useAccountName();
  const message = useFinanceError();
  const id = useId();
  const active = accounts.filter((a) => a.status === "active");
  const [name, setName] = useState(template?.name ?? "");
  const [kind, setKind] = useState<"income" | "expense">(template?.kind ?? "expense");
  const [accountId, setAccountId] = useState(template?.account_id ?? "");
  const [categoryId, setCategoryId] = useState(template?.category_id ?? "");
  const [amount, setAmount] = useState(
    template?.amount ? editableAmount(template.amount.amount, locale) : "",
  );
  const [description, setDescription] = useState(template?.description ?? "");
  const [favourite, setFavourite] = useState(template?.is_favourite ?? true);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const account =
    active.find((a) => a.id === accountId) ??
    active.find((a) => a.id === defaultAccountId) ??
    active[0];
  const currency = account?.currency ?? template?.currency ?? "EUR";
  const exponent = currencies.find((c) => c.code === currency)?.exponent ?? 2;
  const parsed = amount.trim() ? parseAmount(amount, locale, exponent) : null;
  const options = categories.filter((c) => c.kind === kind && c.status === "active");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFailure(null);
    if (!name.trim() || !categoryId || (parsed && !parsed.ok)) {
      setFailure(t("incomplete"));
      return;
    }
    setBusy(true);
    try {
      const money = parsed?.ok ? { amount: parsed.amount, currency } : null;
      if (template) {
        await templatesApi.update(template.id, template.version, {
          name: name.trim(),
          category_id: categoryId,
          description: description.trim() || null,
          is_favourite: favourite,
          ...(money ? { amount: money } : { clear_amount: true }),
          ...(accountId ? { account_id: accountId } : { clear_account: true }),
        });
      } else {
        await templatesApi.create(
          {
            name: name.trim(),
            kind,
            category_id: categoryId,
            currency,
            amount: money,
            account_id: accountId || null,
            description: description.trim() || null,
            is_favourite: favourite,
          },
          key,
        );
      }
      onSaved(template ? t("saved") : t("created"));
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={template ? t("editTitle") : t("newTitle")}
      lead={t("formLead")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {t("save")}
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
            value={name}
            maxLength={60}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        {!template ? (
          <div className={s.segmented} role="group" aria-label={t("kind")}>
            {(["expense", "income"] as const).map((k) => (
              <button
                key={k}
                type="button"
                className={s.segment}
                data-kind={k}
                aria-pressed={kind === k}
                onClick={() => {
                  setKind(k);
                  setCategoryId("");
                }}
              >
                {t(k)}
              </button>
            ))}
          </div>
        ) : null}
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-category`}>
            {t("category")}
          </label>
          <Select id={`${id}-category`} value={categoryId} onValueChange={setCategoryId}>
            <option value="">{t("chooseCategory")}</option>
            {options.map((c) => (
              <option key={c.id} value={c.id}>
                {categoryName(c)}
              </option>
            ))}
          </Select>
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-account`}>
            {t("account")}
          </label>
          <Select id={`${id}-account`} value={accountId} onValueChange={setAccountId}>
            <option value="">{t("defaultAccount")}</option>
            {active.map((a) => (
              <option key={a.id} value={a.id}>
                {`${accountName(a)} · ${a.currency}`}
              </option>
            ))}
          </Select>
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-amount`}>
            {t("amount", { currency })}
          </label>
          <MoneyInput
            id={`${id}-amount`}
            className={appStyles.input}
            value={amount}
            onValueChange={setAmount}
            aria-invalid={parsed && !parsed.ok ? true : undefined}
          />
          <span className={appStyles.hint}>{t("amountHint")}</span>
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-desc`}>
            {t("description")}
          </label>
          <input
            id={`${id}-desc`}
            className={appStyles.input}
            value={description}
            maxLength={200}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <label className={s.inlineCheck}>
          <input
            type="checkbox"
            checked={favourite}
            onChange={(e) => setFavourite(e.target.checked)}
          />
          <span>{t("favouriteLabel")}</span>
        </label>
      </form>
    </AccountDialog>
  );
}
