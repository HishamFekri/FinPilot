import { afterEach, describe, expect, it } from "vitest";
import { canGoBackInApp, installHistoryDepth, resetHistoryDepth } from "./history-depth";

/** A browser history of its own, so the tests never touch jsdom's. */
function fakeWindow(initialState: unknown = null) {
  const entries: { state: unknown; url: string }[] = [{ state: initialState, url: "/en/app" }];
  let index = 0;
  const listeners: ((event: PopStateEvent) => void)[] = [];
  const history = {
    get state() {
      return entries[index]!.state;
    },
    pushState(state: unknown, _unused: string, url?: string | URL | null) {
      entries.splice(index + 1);
      entries.push({ state, url: String(url ?? entries[index]!.url) });
      index += 1;
    },
    replaceState(state: unknown, _unused: string, url?: string | URL | null) {
      entries[index] = { state, url: String(url ?? entries[index]!.url) };
    },
  };
  const go = (delta: number) => {
    index += delta;
    for (const listener of listeners) {
      listener({ state: entries[index]!.state } as PopStateEvent);
    }
  };
  const page = { scrollY: 0, height: 3000 };
  const win = {
    history,
    get scrollY() {
      return page.scrollY;
    },
    innerHeight: 800,
    document: {
      documentElement: {
        get scrollHeight() {
          return page.height;
        },
      },
    },
    scrollTo: (_x: number, y: number) => {
      page.scrollY = y;
    },
    requestAnimationFrame: (callback: () => void) => {
      callback();
      return 1;
    },
    cancelAnimationFrame: () => {},
    addEventListener: (type: string, listener: (event: PopStateEvent) => void) => {
      if (type === "popstate") listeners.push(listener);
    },
    removeEventListener: () => {},
  } as unknown as Window;
  return { win, go, page, entries: () => entries };
}

afterEach(() => resetHistoryDepth());

describe("history depth", () => {
  it("a fresh page load has nothing in-app behind it", () => {
    installHistoryDepth(fakeWindow().win);
    expect(canGoBackInApp()).toBe(false);
  });

  it("pushes go one deeper; Back and Forward restore each entry's depth", () => {
    const { win, go } = fakeWindow();
    installHistoryDepth(win);
    win.history.pushState({ __NA: true }, "", "/en/app/analytics?tab=accounts");
    win.history.pushState({ __NA: true }, "", "/en/app/analytics?tab=trips");
    win.history.pushState({ __NA: true }, "", "/en/app/trips/1");
    expect(canGoBackInApp()).toBe(true);
    go(-1); // tab=trips
    expect(canGoBackInApp()).toBe(true);
    go(-2); // the entry the tab loaded into
    expect(canGoBackInApp()).toBe(false);
    go(+1);
    expect(canGoBackInApp()).toBe(true);
  });

  it("a replace keeps the depth, and Next's own state is kept", () => {
    const { win, entries } = fakeWindow();
    installHistoryDepth(win);
    win.history.replaceState({ __NA: true, tree: 1 }, "", "/en/app/trips/join");
    win.history.replaceState({ __NA: true, tree: 2 }, "", "/en/app/trips/9");
    expect(canGoBackInApp()).toBe(false);
    expect(entries()[0]!.state).toMatchObject({ __NA: true, tree: 2, fpDepth: 0 });
  });

  it("a reload keeps the depth the entry had", () => {
    installHistoryDepth(fakeWindow({ __NA: true, fpDepth: 2 }).win);
    expect(canGoBackInApp()).toBe(true);
  });

  it("Back puts the page's scroll position back", () => {
    const { win, go, page } = fakeWindow();
    installHistoryDepth(win);
    page.scrollY = 900;
    win.history.pushState({ __NA: true }, "", "/en/app/trips");
    page.scrollY = 0;
    go(-1);
    expect(page.scrollY).toBe(900);
  });

  it("ignores a depth that isn't a whole, positive number", () => {
    installHistoryDepth(fakeWindow({ fpDepth: "3" }).win);
    expect(canGoBackInApp()).toBe(false);
  });
});
