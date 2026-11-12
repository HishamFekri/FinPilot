"use client";

import { Archive, ArchiveRestore, ChevronLeft, Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { type FormEvent, useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { type Category, financeApi, type Kind } from "@/lib/finance";
import { cn } from "@/lib/utils";
import { AccountDialog } from "../accounts/parts";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { Button, Notice, Spinner } from "../ui";
import { ConfirmDialog } from "../confirm-dialog";
import { DeleteOrArchive, useRemovalFailure } from "../removal";
import { GroupedCategories } from "./category-groups";
import { FinanceGate, useFinance } from "./finance-provider";
import s from "./finance.module.css";
import {
  CATEGORY_COLORS,
  CATEGORY_ICONS,
  CategoryIcon,
  useCategoryName,
  useFinanceError,
} from "./parts";

export function CategoriesView() {
  return (
    <FinanceGate>
      <Categories />
    </FinanceGate>
  );
}

type Editing = { kind: "create"; type: Kind } | { kind: "edit"; category: Category };

function Categories() {
  const t = useTranslations("app.categories");
  const { handleAuthError } = useAuth();
  const { changed } = useFinance();
  const message = useFinanceError();
  const categoryName = useCategoryName();
  const defaults = useTranslations("app.categories.defaults");
  const [kind, setKind] = useState<Kind>("expense");
  const [all, setAll] = useState<Category[] | null>(null);
  const [failed, setFailed] = useState(false);
  // Each opening mounts the form fresh; the target stays while the dialog animates closed.
  const [editing, setEditing] = useState<{ target: Editing; n: number } | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const edit = (target: Editing) => {
    setEditing((prev) => ({ target, n: (prev?.n ?? 0) + 1 }));
    setEditorOpen(true);
  };
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAll(await financeApi.categories(true));
      setFailed(false);
    } catch (error) {
      if (!handleAuthError(error)) setFailed(true);
    }
  }, [handleAuthError]);

  useEffect(() => {
    let cancelled = false;
    financeApi
      .categories(true)
      .then((list) => {
        if (!cancelled) setAll(list);
      })
      .catch((error: unknown) => {
        if (!cancelled && !handleAuthError(error)) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [handleAuthError]);

  const after = async (text: string) => {
    setNotice({ tone: "success", text });
    await Promise.all([load(), changed()]);
  };

  const setStatus = async (category: Category, status: "active" | "archived") => {
    setBusyId(category.id);
    setNotice(null);
    try {
      await financeApi.updateCategory(category.id, category.version, { status });
      await after(status === "archived" ? t("done.archived") : t("done.restored"));
    } catch (error) {
      if (!handleAuthError(error)) setNotice({ tone: "error", text: message(error) });
    } finally {
      setBusyId(null);
    }
  };

  const [deleting, setDeleting] = useState<Category | null>(null);
  const removalFailure = useRemovalFailure();

  const ofKind = (all ?? []).filter((c) => c.kind === kind);
  const active = ofKind.filter((c) => c.status === "active");
  const archived = ofKind.filter((c) => c.status === "archived");

  const row = (category: Category, groupPicker?: ReactNode) => (
    <li key={category.id} className={s.catRow}>
      <CategoryIcon category={category} />
      <span className={s.rowText}>
        <span className={s.rowTitle} dir="auto">
          {categoryName(category)}
        </span>
        {category.system_key && category.name ? (
          <span className={s.rowMeta}>
            {t("renamed", { name: defaults(category.system_key as "groceries") })}
          </span>
        ) : null}
      </span>
      <span className={s.catActions}>
        {groupPicker}
        {category.status === "active" ? (
          <>
            <Button size="sm" variant="ghost" onClick={() => edit({ kind: "edit", category })}>
              <Pencil aria-hidden="true" />
              <span>{t("edit")}</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              busy={busyId === category.id}
              onClick={() => void setStatus(category, "archived")}
            >
              <Archive aria-hidden="true" />
              <span>{t("archive")}</span>
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            busy={busyId === category.id}
            onClick={() => void setStatus(category, "active")}
          >
            <ArchiveRestore aria-hidden="true" />
            <span>{t("restore")}</span>
          </Button>
        )}
        {category.system_key === null ? (
          <Button size="sm" variant="ghost" onClick={() => setDeleting(category)}>
            <Trash2 aria-hidden="true" />
            <span>{t("delete")}</span>
          </Button>
        ) : null}
      </span>
    </li>
  );

  return (
    <div className={appStyles.narrow}>
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={t("deleteTitle", { name: deleting ? categoryName(deleting) : "" })}
        body={
          <>
            <p>{t("deleteBody")}</p>
            <DeleteOrArchive />
          </>
        }
        confirm={t("delete")}
        onConfirm={async () => {
          if (!deleting) return null;
          try {
            await financeApi.deleteCategory(deleting.id);
            await after(t("done.deleted"));
            return null;
          } catch (error) {
            return removalFailure(error);
          }
        }}
      />
      <Link href="/app/settings" className={s.backLink}>
        <ChevronLeft aria-hidden="true" className={s.flipRtl} />
        {t("back")}
      </Link>
      <div className={s.historyHead}>
        <div>
          <h1 className={appStyles.pageTitle}>{t("title")}</h1>
          <p className={appStyles.pageLead}>{t("lead")}</p>
        </div>
      </div>

      <div className={s.catToolbar}>
        <div className={s.segmented} role="group" aria-label={t("title")}>
          {(["expense", "income"] as const).map((k) => (
            <button
              key={k}
              type="button"
              className={s.segment}
              data-kind={k}
              aria-pressed={kind === k}
              onClick={() => setKind(k)}
            >
              {t(`kinds.${k}`)}
            </button>
          ))}
        </div>
        <Button variant="primary" onClick={() => edit({ kind: "create", type: kind })}>
          <Plus aria-hidden="true" />
          {t("add")}
        </Button>
      </div>

      {notice ? (
        <Notice tone={notice.tone} className={s.gapBelow}>
          {notice.text}
        </Notice>
      ) : null}

      {failed ? (
        <Notice tone="error">
          {t("loadFailed")}{" "}
          <button type="button" className={appStyles.linkBtn} onClick={() => void load()}>
            {t("retry")}
          </button>
        </Notice>
      ) : all === null ? (
        <p className={appStyles.muted} role="status">
          <Spinner /> <span>{t("loading")}</span>
        </p>
      ) : (
        <>
          {kind === "expense" ? (
            <GroupedCategories
              categories={active}
              row={row}
              onChanged={(text) => void after(text)}
            />
          ) : (
            <section className={s.panel} aria-label={t(`kinds.${kind}`)}>
              <ul className={s.rows}>{active.map((c) => row(c))}</ul>
            </section>
          )}
          {archived.length > 0 ? (
            <section className={s.archivedBlock} aria-labelledby="archived-title">
              <h2 id="archived-title" className={s.subTitle}>
                {t("archivedTitle")}
              </h2>
              <p className={cn(appStyles.muted, appStyles.small)}>{t("archivedNote")}</p>
              <div className={s.panel}>
                <ul className={s.rows}>{archived.map((c) => row(c))}</ul>
              </div>
            </section>
          ) : null}
        </>
      )}

      {editing ? (
        <CategoryDialog
          key={editing.n}
          editing={editing.target}
          open={editorOpen}
          onClose={() => setEditorOpen(false)}
          onSaved={(text) => {
            setEditorOpen(false);
            void after(text);
          }}
        />
      ) : null}
    </div>
  );
}

function CategoryDialog({
  editing,
  open,
  onClose,
  onSaved,
}: {
  editing: Editing;
  open: boolean;
  onClose: () => void;
  onSaved: (text: string) => void;
}) {
  const t = useTranslations("app.categories");
  const icons = useTranslations("app.categories.icons");
  const colors = useTranslations("app.categories.colors");
  const defaults = useTranslations("app.categories.defaults");
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const id = useId();
  const existing = editing.kind === "edit" ? editing.category : null;
  const type = editing.kind === "create" ? editing.type : (existing?.kind ?? "expense");
  const defaultName = existing?.system_key ? defaults(existing.system_key as "groceries") : null;

  const [name, setName] = useState(existing ? (existing.name ?? defaultName ?? "") : "");
  const [icon, setIcon] = useState(existing?.icon ?? "tag");
  const [color, setColor] = useState<string>(existing?.color ?? "slate");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const clean = name.trim().replace(/\s+/gu, " ");
  const nameError = !clean
    ? t("form.nameRequired")
    : clean.length > 40
      ? t("form.nameTooLong")
      : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFailure(null);
    if (nameError) return;
    setBusy(true);
    try {
      if (!existing) {
        await financeApi.createCategory({ kind: type, name: clean, icon, color });
        onSaved(t("done.created"));
      } else {
        const changes: Parameters<typeof financeApi.updateCategory>[2] = {};
        // Saving a default under its own default name keeps it translated in every language.
        const current = existing.name ?? defaultName;
        if (clean !== current) changes.name = defaultName && clean === defaultName ? null : clean;
        if (icon !== existing.icon) changes.icon = icon;
        if (color !== existing.color) changes.color = color;
        if (Object.keys(changes).length > 0) {
          await financeApi.updateCategory(existing.id, existing.version, changes);
        }
        onSaved(t("done.saved"));
      }
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AccountDialog
      open={open}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={existing ? t("form.editTitle") : t("form.createTitle", { kind: type })}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("form.cancel")}
          </Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {existing ? t("form.save") : t("form.create")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className={appStyles.form} onSubmit={submit} noValidate>
        <div className={s.catPreview} aria-hidden="true">
          <CategoryIcon category={{ icon, color }} />
          <span dir="auto">{clean || "…"}</span>
        </div>
        <div className={appStyles.field}>
          <label className={appStyles.label} htmlFor={`${id}-name`}>
            {t("form.name")}
          </label>
          <input
            id={`${id}-name`}
            className={appStyles.input}
            dir="auto"
            maxLength={60}
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-invalid={submitted && nameError ? true : undefined}
            aria-describedby={`${id}-name-error`}
          />
          <span id={`${id}-name-error`} className={appStyles.error} aria-live="polite">
            {submitted && nameError ? nameError : ""}
          </span>
          {existing?.name && defaultName ? (
            <button
              type="button"
              className={cn(appStyles.linkBtn, s.alignStart)}
              onClick={() => setName(defaultName)}
            >
              {t("form.resetName")}
            </button>
          ) : null}
        </div>
        <fieldset className={s.iconGrid}>
          <legend className={appStyles.label}>{t("form.icon")}</legend>
          {Object.entries(CATEGORY_ICONS).map(([key, Icon]) => (
            <label key={key} className={s.iconOption} title={icons(key as "tag")}>
              <input
                type="radio"
                name={`${id}-icon`}
                value={key}
                checked={icon === key}
                onChange={() => setIcon(key)}
                aria-label={icons(key as "tag")}
              />
              <Icon aria-hidden="true" />
            </label>
          ))}
        </fieldset>
        <fieldset className={s.swatches}>
          <legend className={appStyles.label}>{t("form.color")}</legend>
          {CATEGORY_COLORS.map((key) => (
            <label key={key} className={s.swatch} title={colors(key)}>
              <input
                type="radio"
                name={`${id}-color`}
                value={key}
                checked={color === key}
                onChange={() => setColor(key)}
                aria-label={colors(key)}
              />
              <span className={s.catIcon} data-color={key} aria-hidden="true" />
            </label>
          ))}
        </fieldset>
        {failure ? <Notice tone="error">{failure}</Notice> : null}
      </form>
    </AccountDialog>
  );
}
