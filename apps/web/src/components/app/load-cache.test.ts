import { describe, expect, it } from "vitest";
import { forgetLoads, recall, remember } from "./load-cache";

describe("load cache", () => {
  it("returns the last answer for a key, and nothing for an unknown one", () => {
    remember("summary:2026-11|0", { total: 1 });
    remember("summary:2026-11|0", { total: 2 });
    expect(recall("summary:2026-11|0")).toEqual({ total: 2 });
    // A new finance revision is a different key: an answer from before a change is never reused.
    expect(recall("summary:2026-11|1")).toBeUndefined();
  });

  it("keeps only the most recent answers", () => {
    forgetLoads();
    for (let i = 0; i < 100; i += 1) remember(`k${i}|0`, i);
    expect(recall("k0|0")).toBeUndefined();
    expect(recall("k99|0")).toBe(99);
  });

  it("forgets everything when asked", () => {
    remember("a|0", 1);
    forgetLoads();
    expect(recall("a|0")).toBeUndefined();
  });
});
