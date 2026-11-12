"use client";

import { Archive, Check, Pencil, Plus, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { type CategoryGroup, groupsApi } from "@/lib/everyday";
import type { Category } from "@/lib/finance";
import appStyles from "../app.module.css";
import { useAuth } from "../auth-provider";
import { Button, Notice } from "../ui";
import s from "./finance.module.css";
import { useCategoryName, useFinanceError } from "./parts";
import { Select } from "../select";
import { useConfirm } from "../confirm-dialog";

/** A group's name: a default's translation unless the person renamed it. */
export function useGroupName() {
  const t = useTranslations("app.categories.groups.defaults");
  return (g: Pick<CategoryGroup, "system_key" | "name"> | null | undefined, fallback = "") =>
    g?.name ?? (g?.system_key ? t(g.system_key as "essentials") : fallback);
}

export function useGroups(stamp: number) {
  const { handleAuthError } = useAuth();
  const [groups, setGroups] = useState<{ for: number; list?: CategoryGroup[] }>({ for: -1 });
  useEffect(() => {
    let cancelled = false;
    groupsApi
      .list()
      .then((list) => {
        if (!cancelled) setGroups({ for: stamp, list });
      })
      .catch((error: unknown) => {
        if (!cancelled) handleAuthError(error);
      });
    return () => {
      cancelled = true;
    };
  }, [stamp, handleAuthError]);
  return groups.list ?? null;
}

/**
 * Expense categories arranged in groups. Moving a category or renaming or archiving a group
 * changes no amount: a group's total is its categories' totals, each counted once. Archiving a
 * group leaves its categories ungrouped.
 */
export function GroupedCategories({
  categories,
  row,
  onChanged,
}: {
  categories: Category[];
  row: (category: Category, groupPicker: ReactNode) => ReactNode;
  onChanged: (text: string) => void;
}) {
  const t = useTranslations("app.categories.groups");
  const [confirmEl, ask] = useConfirm();
  const { handleAuthError } = useAuth();
  const message = useFinanceError();
  const groupName = useGroupName();
  const categoryName = useCategoryName();
  const [stamp, setStamp] = useState(0);
  const groups = useGroups(stamp);
  const [failure, setFailure] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const [creating, setCreating] = useState<string | null>(null);

  const run = async (job: () => Promise<unknown>, done: string) => {
    setFailure(null);
    try {
      await job();
      setStamp((n) => n + 1);
      onChanged(done);
    } catch (error) {
      if (!handleAuthError(error)) setFailure(message(error));
    }
  };

  if (!groups) return null;
  const known = new Set(groups.map((g) => g.id));
  const sections: { group: CategoryGroup | null; items: Category[] }[] = [
    ...groups.map((g) => ({ group: g, items: categories.filter((c) => c.group_id === g.id) })),
    {
      group: null,
      items: categories.filter((c) => !c.group_id || !known.has(c.group_id)),
    },
  ];

  const picker = (c: Category) => (
    <Select
      size="sm"
      className={s.groupSelect}
      aria-label={t("moveLabel", { name: categoryName(c) })}
      value={c.group_id && known.has(c.group_id) ? c.group_id : ""}
      onValueChange={(value) => void run(() => groupsApi.move(c.id, value || null), t("moved"))}
    >
      {groups.map((g) => (
        <option key={g.id} value={g.id}>
          {groupName(g)}
        </option>
      ))}
      <option value="">{t("ungrouped")}</option>
    </Select>
  );

  const create = (event: FormEvent) => {
    event.preventDefault();
    if (!creating?.trim()) return;
    void run(() => groupsApi.create(creating.trim()), t("created")).then(() => setCreating(null));
  };

  return (
    <div className={s.groupStack}>
      {confirmEl}
      {failure ? <Notice tone="error">{failure}</Notice> : null}
      {sections.map(({ group, items }) =>
        group === null && items.length === 0 ? null : (
          <section
            key={group?.id ?? "none"}
            className={s.panel}
            aria-label={groupName(group, t("ungrouped"))}
          >
            <div className={s.groupHead}>
              {renaming && group && renaming.id === group.id ? (
                <form
                  className={s.groupRename}
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!renaming.name.trim()) return;
                    void run(
                      () =>
                        groupsApi.update(group.id, group.version, { name: renaming.name.trim() }),
                      t("renamed"),
                    ).then(() => setRenaming(null));
                  }}
                >
                  <input
                    className={appStyles.input}
                    value={renaming.name}
                    maxLength={40}
                    aria-label={t("nameLabel")}
                    autoFocus
                    onChange={(e) => setRenaming({ id: group.id, name: e.target.value })}
                  />
                  <button type="submit" className={s.iconAction} aria-label={t("save")}>
                    <Check aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className={s.iconAction}
                    aria-label={t("cancel")}
                    onClick={() => setRenaming(null)}
                  >
                    <X aria-hidden="true" />
                  </button>
                </form>
              ) : (
                <h2 className={s.groupTitle} dir="auto">
                  {groupName(group, t("ungrouped"))}
                  <span className={s.groupCount}>{items.length}</span>
                </h2>
              )}
              {group && !(renaming && renaming.id === group.id) ? (
                <span className={s.templateActions}>
                  <button
                    type="button"
                    className={s.iconAction}
                    aria-label={t("rename", { name: groupName(group) })}
                    onClick={() => setRenaming({ id: group.id, name: groupName(group) })}
                  >
                    <Pencil aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className={s.iconAction}
                    aria-label={t("archive", { name: groupName(group) })}
                    onClick={async () => {
                      if (await ask({ title: t("archiveConfirm", { name: groupName(group) }) }))
                        void run(
                          () => groupsApi.update(group.id, group.version, { status: "archived" }),
                          t("archived"),
                        );
                    }}
                  >
                    <Archive aria-hidden="true" />
                  </button>
                </span>
              ) : null}
            </div>
            {items.length > 0 ? (
              <ul className={s.rows}>{items.map((c) => row(c, picker(c)))}</ul>
            ) : (
              <p className={s.groupEmpty}>{t("empty")}</p>
            )}
          </section>
        ),
      )}
      {creating === null ? (
        <Button variant="ghost" onClick={() => setCreating("")}>
          <Plus aria-hidden="true" />
          {t("new")}
        </Button>
      ) : (
        <form className={s.groupRename} onSubmit={create}>
          <input
            className={appStyles.input}
            value={creating}
            maxLength={40}
            placeholder={t("newPlaceholder")}
            aria-label={t("nameLabel")}
            autoFocus
            onChange={(e) => setCreating(e.target.value)}
          />
          <Button type="submit" size="sm" variant="primary">
            {t("create")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setCreating(null)}>
            {t("cancel")}
          </Button>
        </form>
      )}
      <p className={s.note}>{t("rule")}</p>
    </div>
  );
}
