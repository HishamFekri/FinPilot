import { describe, expect, it } from "vitest";
import {
  DEFAULT_LAYOUT,
  hideWidget,
  type Layout,
  normalize,
  placeWidget,
  positionOf,
  showWidget,
  stepWidget,
  WIDGETS,
} from "./home-layout";

const all = (l: Layout) => [...l.rows.flat(), ...l.hidden];

describe("home layout", () => {
  it("starts from the default when nothing is saved or it's unusable", () => {
    expect(normalize(null)).toEqual(DEFAULT_LAYOUT);
    expect(normalize({ rows: "nope" })).toEqual(DEFAULT_LAYOUT);
    expect(normalize({ columns: [[], []] })).toEqual(DEFAULT_LAYOUT);
  });

  it("follows the approved design by default", () => {
    expect(DEFAULT_LAYOUT.rows).toEqual([
      ["summary"],
      ["recent", "categories"],
      ["upcoming", "budgets", "goals"],
    ]);
    expect(all(DEFAULT_LAYOUT).sort()).toEqual([...WIDGETS].sort());
  });

  it("moves a section beside others, and onto a row of its own", () => {
    const beside = placeWidget(DEFAULT_LAYOUT, "goals", { kind: "row", row: 1, index: 0 });
    expect(beside.rows[1]).toEqual(["goals", "recent", "categories"]);
    expect(beside.rows[2]).toEqual(["upcoming", "budgets"]);
    const own = placeWidget(DEFAULT_LAYOUT, "categories", { kind: "gap", row: 0 });
    expect(own.rows[0]).toEqual(["categories"]);
    expect(own.rows[2]).toEqual(["recent"]);
    expect(all(own).sort()).toEqual([...WIDGETS].sort());
  });

  it("never puts four sections on a row, and drops a row it emptied", () => {
    const full = placeWidget(DEFAULT_LAYOUT, "summary", { kind: "row", row: 2, index: 0 });
    expect(full).toBe(DEFAULT_LAYOUT);
    const merged = placeWidget(DEFAULT_LAYOUT, "summary", { kind: "row", row: 1, index: 0 });
    expect(merged.rows).toHaveLength(2);
  });

  it("walks every place with up and down, and trades places with left and right", () => {
    let l: Layout = DEFAULT_LAYOUT;
    l = stepWidget(l, "categories", "up")!; // out of the shared row, onto its own above
    expect(l.rows.slice(0, 3)).toEqual([["summary"], ["categories"], ["recent"]]);
    l = stepWidget(l, "categories", "up")!; // beside the summary
    expect(l.rows[0]).toEqual(["summary", "categories"]);
    l = stepWidget(l, "categories", "before")!;
    expect(l.rows[0]).toEqual(["categories", "summary"]);
    expect(stepWidget(l, "categories", "before")).toBeNull();
    l = stepWidget(l, "categories", "first")!;
    expect(positionOf(l, "categories")).toEqual({ row: 0, index: 0 });
    expect(stepWidget(l, "categories", "up")).toBeNull();
    l = stepWidget(l, "categories", "last")!;
    expect(l.rows.at(-1)).toEqual(["categories"]);
    expect(stepWidget(l, "categories", "down")).toBeNull();
    // Down past a full row lands on a row of its own after it.
    const d = stepWidget(DEFAULT_LAYOUT, "summary", "down")!;
    expect(d.rows[0]).toEqual(["summary", "recent", "categories"]);
    const e = stepWidget(d, "summary", "down")!;
    expect(e.rows[1]).toEqual(["summary"]);
    const f = stepWidget(e, "summary", "down")!;
    expect(f.rows.at(-1)).toEqual(["summary"]);
  });

  it("hides and shows any section", () => {
    const hidden = hideWidget(DEFAULT_LAYOUT, "summary");
    expect(hidden.hidden).toContain("summary");
    expect(hidden.rows[0]).toEqual(["recent", "categories"]);
    const shown = showWidget(hidden, "summary");
    expect(shown.hidden).not.toContain("summary");
    expect(shown.rows.at(-1)).toEqual(["summary"]);
  });

  it("repairs a saved layout: unknown, repeated and new sections", () => {
    const repaired = normalize({
      rows: [["recent", "mystery", "recent"], ["categories"]],
      hidden: ["links"],
    });
    expect(repaired.rows[0]).toEqual(["recent"]);
    expect(repaired.hidden).toEqual(["links", "templates"]);
    expect(all(repaired).sort()).toEqual([...WIDGETS].sort());
    expect(new Set(all(repaired)).size).toBe(WIDGETS.length);
  });

  it("keeps rows to three when a saved one is too long, without losing a section", () => {
    const repaired = normalize({
      rows: [["summary", "recent", "categories", "goals"]],
      hidden: [],
    });
    expect(repaired.rows[0]).toEqual(["summary", "recent", "categories"]);
    expect(all(repaired).sort()).toEqual([...WIDGETS].sort());
  });
});
