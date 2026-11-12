import { api } from "@/lib/api";

/**
 * Home's arrangement: rows of one to three sections, and the sections the person hid. Saved per
 * person on the server (`/auth/me/home-layout`), so it follows them across devices and sign-ins.
 * Pure helpers here; the dashboard component does the interaction.
 */

export const WIDGETS = [
  "summary",
  "recent",
  "categories",
  "upcoming",
  "budgets",
  "goals",
  "templates",
  "links",
] as const;
export type WidgetId = (typeof WIDGETS)[number];

export const MAX_PER_ROW = 3;

export type Layout = { rows: WidgetId[][]; hidden: WidgetId[] };

export const DEFAULT_LAYOUT: Layout = {
  rows: [["summary"], ["recent", "categories"], ["upcoming", "budgets", "goals"]],
  hidden: ["templates", "links"],
};

const isWidget = (value: unknown): value is WidgetId =>
  typeof value === "string" && (WIDGETS as readonly string[]).includes(value);

/**
 * A saved layout made safe to show: unknown or repeated sections dropped, rows kept to three, and
 * sections added since it was saved put back the way the default has them.
 */
export function normalize(saved: unknown): Layout {
  const raw = (saved ?? null) as { rows?: unknown; hidden?: unknown } | null;
  if (!raw || !Array.isArray(raw.rows)) return structuredClone(DEFAULT_LAYOUT);
  const seen = new Set<WidgetId>();
  const take = (list: unknown): WidgetId[] =>
    (Array.isArray(list) ? list : []).filter((w): w is WidgetId => {
      if (!isWidget(w) || seen.has(w)) return false;
      seen.add(w);
      return true;
    });
  const rows = raw.rows.map((row) => take(row).slice(0, MAX_PER_ROW)).filter((row) => row.length);
  // Anything cut from an over-long row is lost from `seen` too, so it's placed again below.
  seen.clear();
  rows.flat().forEach((w) => seen.add(w));
  const hidden = take(raw.hidden);
  for (const w of WIDGETS) {
    if (seen.has(w)) continue;
    if (DEFAULT_LAYOUT.hidden.includes(w)) hidden.push(w);
    else rows.push([w]);
  }
  return { rows, hidden };
}

export type Position = { row: number; index: number };

/** Where a section is. */
export function positionOf(layout: Layout, id: WidgetId): Position | null {
  for (let row = 0; row < layout.rows.length; row += 1) {
    const index = layout.rows[row]?.indexOf(id) ?? -1;
    if (index >= 0) return { row, index };
  }
  return null;
}

/**
 * Where a section can go. `gap`: a row of its own, before row `row`. `row`: beside others, at
 * `index` among them. Row numbers are the current ones.
 */
export type Target = { kind: "gap"; row: number } | { kind: "row"; row: number; index: number };

export function placeWidget(layout: Layout, id: WidgetId, target: Target): Layout {
  const rows = layout.rows.map((row) => row.filter((w) => w !== id));
  if (target.kind === "gap") {
    rows.splice(target.row, 0, [id]);
  } else {
    const row = rows[target.row];
    if (!row || row.length >= MAX_PER_ROW) return layout;
    row.splice(Math.max(0, Math.min(target.index, row.length)), 0, id);
  }
  return { rows: rows.filter((row) => row.length), hidden: layout.hidden.filter((w) => w !== id) };
}

export type Step = "up" | "down" | "before" | "after" | "first" | "last";

/**
 * One keyboard move, or null when there's nowhere to go. Left and right trade places in a row. Up
 * and down walk every place in turn: out of a shared row onto its own, then into the next row.
 */
export function stepWidget(layout: Layout, id: WidgetId, step: Step): Layout | null {
  const at = positionOf(layout, id);
  if (!at) return null;
  const row = layout.rows[at.row];
  if (!row) return null;
  const alone = row.length === 1;
  let target: Target | null = null;
  if (step === "before" && at.index > 0) target = { kind: "row", row: at.row, index: at.index - 1 };
  if (step === "after" && at.index < row.length - 1)
    target = { kind: "row", row: at.row, index: at.index + 1 };
  if (step === "up") {
    const above = layout.rows[at.row - 1];
    if (!alone) target = { kind: "gap", row: at.row };
    else if (above)
      target =
        above.length < MAX_PER_ROW
          ? { kind: "row", row: at.row - 1, index: above.length }
          : { kind: "gap", row: at.row - 1 };
  }
  if (step === "down") {
    const below = layout.rows[at.row + 1];
    if (!alone) target = { kind: "gap", row: at.row + 1 };
    else if (below)
      target =
        below.length < MAX_PER_ROW
          ? { kind: "row", row: at.row + 1, index: 0 }
          : { kind: "gap", row: at.row + 2 };
  }
  if (step === "first") target = { kind: "gap", row: 0 };
  if (step === "last") target = { kind: "gap", row: layout.rows.length };
  if (!target) return null;
  const next = placeWidget(layout, id, target);
  return sameLayout(next, layout) ? null : next;
}

export function hideWidget(layout: Layout, id: WidgetId): Layout {
  return {
    rows: layout.rows.map((row) => row.filter((w) => w !== id)).filter((row) => row.length),
    hidden: [...layout.hidden.filter((w) => w !== id), id],
  };
}

/** Shows a hidden section again, on a row of its own at the end. */
export function showWidget(layout: Layout, id: WidgetId): Layout {
  return placeWidget(layout, id, { kind: "gap", row: layout.rows.length });
}

export function sameLayout(a: Layout, b: Layout): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export const homeLayoutApi = {
  get: () => api<{ layout: Layout | null; widgets: string[] }>("/auth/me/home-layout"),
  save: (layout: Layout | null) =>
    api<{ layout: Layout | null }>("/auth/me/home-layout", {
      method: "PUT",
      body: layout ?? undefined,
    }),
};
