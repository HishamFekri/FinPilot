import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";
import ar from "../messages/ar.json";
import en from "../messages/en.json";
import tr from "../messages/tr.json";

/**
 * The owner removed the standalone sign-in page: the landing page is the only way in. These fail
 * if the page, or a link to it, comes back. Only the proxy may name the old URL, to redirect it.
 */
const SRC = join(process.cwd(), "src");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("the standalone sign-in page stays removed", () => {
  it("has no route and no view", () => {
    expect(existsSync(join(SRC, "app", "[locale]", "sign-in"))).toBe(false);
    expect(existsSync(join(SRC, "components", "app", "sign-in-view.tsx"))).toBe(false);
  });

  it("is linked from nowhere except the proxy's redirect", () => {
    const linking = sourceFiles(SRC)
      .filter((file) => /\/sign-in\b/.test(readFileSync(file, "utf8")))
      .map((file) => relative(SRC, file).split(sep).join("/"));
    expect(linking).toEqual(["proxy.ts"]);
  });

  it("has no copy left in any language", () => {
    for (const catalog of [en, ar, tr]) {
      expect(Object.keys(catalog.signIn).sort()).toEqual([
        "dismiss",
        "errors",
        "notices",
        "retry",
        "signInAgain",
      ]);
    }
    expect(JSON.stringify(en)).not.toContain("Sign in to FinPilot");
  });
});
