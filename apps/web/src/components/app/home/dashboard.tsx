"use client";

import { Eye, EyeOff, GripVertical, RotateCcw, SlidersHorizontal } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import {
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { flushSync } from "react-dom";
import { getDirection, type Locale } from "@/i18n/routing";
import {
  DEFAULT_LAYOUT,
  hideWidget,
  homeLayoutApi,
  type Layout,
  MAX_PER_ROW,
  normalize,
  placeWidget,
  positionOf,
  sameLayout,
  showWidget,
  type Step,
  stepWidget,
  type Target,
  type WidgetId,
} from "@/lib/home-layout";
import { useAuth } from "../auth-provider";
import { ConfirmDialog } from "../confirm-dialog";
import { Button } from "../ui";
import s from "./dashboard.module.css";

export type Widget = { title: string; node: ReactNode };

type Line = { x: number; y: number; length: number; vertical: boolean };
type Drag = { id: WidgetId; target: Target | null; line: Line | null; pointerId: number };

/** How many of twelve columns a section takes, by its place in its row. */
function spanOf(rowLength: number, index: number): number {
  if (rowLength === 1) return 12;
  if (rowLength === 2) return index === 0 ? 7 : 5;
  return 4;
}

/**
 * Home's sections in rows, arranged by the person. "Customize" turns on editing: each section gets
 * a drag handle (mouse, pen or touch), arrow keys move the focused one (mirrored in Arabic), and
 * any section can be hidden and shown again. While editing, section contents are inert, so
 * dragging can't press their buttons, type in their fields or scroll their charts.
 *
 * Every change is saved to the person's profile on its own (a short pause batches quick moves), so
 * the layout comes back after a refresh or on another device. On narrow screens each row stacks,
 * in the saved order.
 */
export function Dashboard({
  widgets,
  heading,
  action,
}: {
  widgets: Record<WidgetId, Widget>;
  heading: ReactNode;
  /** Always available, editing or not. */
  action: ReactNode;
}) {
  const t = useTranslations("app.dashboard");
  const locale = useLocale() as Locale;
  const rtl = getDirection(locale) === "rtl";
  const { handleAuthError } = useAuth();
  const id = useId();
  const [layout, setLayout] = useState<Layout>(DEFAULT_LAYOUT);
  const [editing, setEditing] = useState(false);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [save, setSave] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [resetting, setResetting] = useState(false);
  const pending = useRef<{ layout: Layout | null; timer: number } | null>(null);
  const grid = useRef<HTMLDivElement>(null);
  const handles = useRef(new Map<WidgetId, HTMLButtonElement>());
  const [focusAfter, setFocusAfter] = useState<WidgetId | null>(null);

  // The saved layout, once; the default shows until then.
  useEffect(() => {
    let cancelled = false;
    homeLayoutApi
      .get()
      .then((answer) => {
        if (!cancelled && answer.layout) setLayout(normalize(answer.layout));
      })
      .catch((error: unknown) => {
        if (!cancelled) handleAuthError(error);
      });
    return () => {
      cancelled = true;
    };
  }, [handleAuthError]);

  // Keyboard moves keep focus on the handle of the section that moved.
  useEffect(() => {
    if (focusAfter) {
      handles.current.get(focusAfter)?.focus();
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFocusAfter(null);
    }
  }, [focusAfter, layout]);

  const persist = (next: Layout | null) => {
    if (pending.current) window.clearTimeout(pending.current.timer);
    setSave("saving");
    const timer = window.setTimeout(async () => {
      pending.current = null;
      try {
        await homeLayoutApi.save(next);
        setSave("saved");
      } catch (error) {
        if (!handleAuthError(error)) setSave("failed");
      }
    }, 400);
    pending.current = { layout: next, timer };
  };

  // A save still waiting when the page closes is sent at once.
  useEffect(() => {
    const flush = () => {
      if (!pending.current) return;
      window.clearTimeout(pending.current.timer);
      void homeLayoutApi.save(pending.current.layout).catch(() => undefined);
      pending.current = null;
    };
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, []);

  /** Sections glide to their new places where the browser can, and at once where it can't. */
  const animate = (update: () => void) => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!document.startViewTransition || reduced) update();
    else document.startViewTransition(() => flushSync(update));
  };

  const change = (next: Layout, message?: string) => {
    if (sameLayout(next, layout)) return;
    animate(() => setLayout(next));
    persist(next);
    if (message) setAnnouncement(message);
  };

  const title = (w: WidgetId) => widgets[w].title;
  const describe = (next: Layout, w: WidgetId) => {
    const at = positionOf(next, w);
    return at ? t("moved", { name: title(w), row: at.row + 1, position: at.index + 1 }) : "";
  };

  // --- Keyboard: arrows on a handle move its section.
  const onHandleKey = (event: KeyboardEvent<HTMLButtonElement>, w: WidgetId) => {
    const before: Step = "before";
    const after: Step = "after";
    const steps: Record<string, Step> = {
      ArrowUp: "up",
      ArrowDown: "down",
      ArrowLeft: rtl ? after : before,
      ArrowRight: rtl ? before : after,
      Home: "first",
      End: "last",
    };
    const step = steps[event.key];
    if (!step) return;
    event.preventDefault();
    const next = stepWidget(layout, w, step);
    if (!next) return;
    change(next, describe(next, w));
    setFocusAfter(w);
  };

  // --- Pointer (mouse, pen, touch): drag a handle; a line shows where it will land.
  const dropAt = (x: number, y: number, w: WidgetId): Pick<Drag, "target" | "line"> => {
    const box = grid.current?.getBoundingClientRect();
    if (!box) return { target: null, line: null };
    const rect = (id: WidgetId) =>
      grid.current?.querySelector(`[data-widget='${id}']`)?.getBoundingClientRect();
    const rows = layout.rows.map((row) => {
      const items = row.map((id) => ({ id, r: rect(id)! })).filter((item) => item.r);
      return {
        items,
        others: items.filter((item) => item.id !== w),
        top: Math.min(...items.map((i) => i.r.top)),
        bottom: Math.max(...items.map((i) => i.r.bottom)),
      };
    });
    const across = (at: number): Line => ({
      x: 0,
      y: at - box.top,
      length: box.width,
      vertical: false,
    });
    const stacked =
      window.getComputedStyle(grid.current!).gridTemplateColumns.split(" ").length < 2;

    if (stacked) {
      // One column: count the sections above the pointer, then find that place in the rows.
      const flat = rows.flatMap((row, r) => row.others.map((item, i) => ({ ...item, row: r, i })));
      const k = flat.filter((item) => item.r.top + item.r.height / 2 < y).length;
      const next = flat[k];
      const lineY = next ? next.r.top - 8 : (flat.at(-1)?.r.bottom ?? box.top) + 8;
      if (!next) return { target: { kind: "gap", row: rows.length }, line: across(lineY) };
      const at = next;
      const room = (rows[at.row]?.others.length ?? 0) < MAX_PER_ROW;
      const target: Target =
        at.i > 0 && room ? { kind: "row", row: at.row, index: at.i } : { kind: "gap", row: at.row };
      return { target, line: across(lineY) };
    }

    for (let r = 0; r < rows.length; r += 1) {
      const row = rows[r]!;
      if (y >= row.bottom + 22) continue;
      const height = row.bottom - row.top;
      const edge = Math.min(48, height * 0.2);
      if (y < row.top + edge || row.others.length === 0)
        return { target: { kind: "gap", row: r }, line: across(row.top - 12) };
      if (y > row.bottom - edge)
        return { target: { kind: "gap", row: r + 1 }, line: across(row.bottom + 12) };
      if (row.others.length >= MAX_PER_ROW) {
        const below = y > row.top + height / 2;
        return {
          target: { kind: "gap", row: below ? r + 1 : r },
          line: across(below ? row.bottom + 12 : row.top - 12),
        };
      }
      // Beside the others: how many has the pointer passed, in reading order?
      const index = row.others.filter((item) => {
        const middle = item.r.left + item.r.width / 2;
        return rtl ? x < middle : x > middle;
      }).length;
      const start = index > 0 ? row.others[index - 1]!.r : null;
      const lineX = start
        ? rtl
          ? start.left - 10
          : start.right + 10
        : rtl
          ? row.others[0]!.r.right + 10
          : row.others[0]!.r.left - 10;
      return {
        target: { kind: "row", row: r, index },
        line: { x: lineX - box.left, y: row.top - box.top, length: height, vertical: true },
      };
    }
    const last = rows.at(-1)?.bottom ?? box.top;
    return { target: { kind: "gap", row: rows.length }, line: across(last + 12) };
  };

  const onPointerDown = (event: PointerEvent<HTMLButtonElement>, w: WidgetId) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ id: w, target: null, line: null, pointerId: event.pointerId });
  };
  const onPointerMove = (event: PointerEvent<HTMLButtonElement>) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    setDrag({ ...drag, ...dropAt(event.clientX, event.clientY, drag.id) });
    // Near the window's edge, scroll so far-away places can be reached.
    const edge = 56;
    if (event.clientY < edge) window.scrollBy(0, -12);
    else if (event.clientY > window.innerHeight - edge) window.scrollBy(0, 12);
  };
  const onPointerUp = (event: PointerEvent<HTMLButtonElement>) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    setDrag(null);
    if (!drag.target) return;
    const next = placeWidget(layout, drag.id, drag.target);
    change(next, describe(next, drag.id));
  };

  const reset = async () => {
    animate(() => setLayout(structuredClone(DEFAULT_LAYOUT)));
    persist(null);
    setAnnouncement(t("resetDone"));
    return null;
  };

  return (
    <div className={s.root} data-dragging={drag ? "" : undefined}>
      <header className={s.head}>
        <div className={s.heading}>{heading}</div>
        <div className={s.headActions}>
          {editing ? (
            <>
              <span className={s.saveState} role="status">
                {save === "saving" ? t("saving") : save === "saved" ? t("saved") : null}
              </span>
              <Button size="sm" variant="ghost" onClick={() => setResetting(true)}>
                <RotateCcw aria-hidden="true" />
                {t("reset")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                {t("done")}
              </Button>
            </>
          ) : (
            <button
              type="button"
              className={s.customize}
              aria-label={t("customize")}
              title={t("customize")}
              onClick={() => setEditing(true)}
            >
              <SlidersHorizontal aria-hidden="true" />
              <span className={s.customizeLabel}>{t("customizeShort")}</span>
            </button>
          )}
          {action}
        </div>
      </header>

      {editing ? (
        <p id={`${id}-help`} className={s.help}>
          {t("help")}
        </p>
      ) : null}
      {save === "failed" ? (
        <p className={s.failed} role="alert">
          {t("saveFailed")}{" "}
          <button type="button" className={s.retry} onClick={() => persist(layout)}>
            {t("retry")}
          </button>
        </p>
      ) : null}
      {editing && layout.hidden.length > 0 ? (
        <div className={s.hiddenList} aria-label={t("hiddenTitle")} role="group">
          <span className={s.hiddenTitle}>{t("hiddenTitle")}</span>
          {layout.hidden.map((w) => (
            <button
              key={w}
              type="button"
              className={s.showChip}
              onClick={() => change(showWidget(layout, w), t("shown", { name: title(w) }))}
            >
              <Eye aria-hidden="true" />
              {t("show", { name: title(w) })}
            </button>
          ))}
        </div>
      ) : null}

      <div className={s.frame}>
        <div className={s.grid} ref={grid}>
          {layout.rows.flatMap((row, r) =>
            row.map((w, i) => (
              <section
                key={w}
                className={s.widget}
                data-widget={w}
                data-row={r}
                data-editing={editing || undefined}
                data-dragged={drag?.id === w || undefined}
                aria-label={editing ? title(w) : undefined}
                style={
                  {
                    "--span": spanOf(row.length, i),
                    ...(editing ? { viewTransitionName: `widget-${w}` } : {}),
                  } as CSSProperties
                }
              >
                {editing ? (
                  <div className={s.bar}>
                    <button
                      type="button"
                      className={s.handle}
                      ref={(el) => {
                        if (el) handles.current.set(w, el);
                        else handles.current.delete(w);
                      }}
                      aria-label={t("move", { name: title(w) })}
                      aria-describedby={`${id}-help`}
                      onKeyDown={(e) => onHandleKey(e, w)}
                      onPointerDown={(e) => onPointerDown(e, w)}
                      onPointerMove={onPointerMove}
                      onPointerUp={onPointerUp}
                      onPointerCancel={() => setDrag(null)}
                    >
                      <GripVertical aria-hidden="true" />
                    </button>
                    <span className={s.barTitle}>{title(w)}</span>
                    <button
                      type="button"
                      className={s.barAction}
                      onClick={() => change(hideWidget(layout, w), t("hidden", { name: title(w) }))}
                    >
                      <EyeOff aria-hidden="true" />
                      {t("hide")}
                    </button>
                  </div>
                ) : null}
                {/* Inert while editing: nothing inside can be used by accident. */}
                <div className={s.content} inert={editing || undefined}>
                  {widgets[w].node}
                </div>
              </section>
            )),
          )}
        </div>
        {drag?.line ? (
          <div
            className={s.dropLine}
            data-vertical={drag.line.vertical || undefined}
            aria-hidden="true"
            style={
              {
                insetBlockStart: drag.line.y,
                left: drag.line.x,
                [drag.line.vertical ? "blockSize" : "inlineSize"]: drag.line.length,
              } as CSSProperties
            }
          />
        ) : null}
      </div>
      <p className={s.srOnly} aria-live="polite">
        {announcement}
      </p>
      <ConfirmDialog
        open={resetting}
        onOpenChange={setResetting}
        title={t("resetTitle")}
        body={<p>{t("resetBody")}</p>}
        confirm={t("reset")}
        tone="primary"
        onConfirm={reset}
      />
    </div>
  );
}
