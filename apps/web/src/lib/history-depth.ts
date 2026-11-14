/**
 * Two things each history entry remembers, so the browser's Back and Forward (and the on-screen
 * Back, see BackLink) feel like stepping between pages rather than reloading them:
 *
 * - **Depth:** how many in-app steps it is from where this tab entered FinPilot. An on-screen Back
 *   follows history when there is an in-app page behind it, and opens a parent page when there
 *   isn't (a direct link, a new tab, a bookmark).
 * - **Scroll position:** where the page was scrolled when it was left. Pages draw their content
 *   after the browser has already tried to restore the position, so it's put back here once the
 *   page is tall enough again.
 *
 * Both are written into history.state when the entry is written: a push is one deeper than the
 * entry it leaves, with a new key; a replace keeps the depth and key of the entry it replaces.
 * Next.js keeps custom state on Back and Forward, and a reload keeps history.state, so they stay
 * right through both. An entry without them (a fresh page load) is depth 0.
 */

type State = Record<string, unknown> | null;

const DEPTH = "fpDepth";
const KEY = "fpKey";
const STORE = "fp_scroll";
const STORE_LIMIT = 50;
const RESTORE_FOR_MS = 2000;

let current = 0;
let currentKey = "";
let installed = false;
let positions = new Map<string, number>();

function depthOf(state: unknown): number {
  const value = (state as State)?.[DEPTH];
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : 0;
}

function keyOf(state: unknown): string {
  const value = (state as State)?.[KEY];
  return typeof value === "string" && /^[a-z0-9]{1,16}$/.test(value) ? value : "";
}

function newKey(): string {
  return Math.random().toString(36).slice(2, 10);
}

function tagged(state: unknown, depth: number, key: string): State {
  const base = state && typeof state === "object" ? (state as Record<string, unknown>) : {};
  return { ...base, [DEPTH]: depth, [KEY]: key };
}

// Only numbers keyed by random ids: nothing about the person or their money.
function load(storage: Storage | undefined): Map<string, number> {
  try {
    const raw = JSON.parse(storage?.getItem(STORE) ?? "[]") as unknown;
    if (!Array.isArray(raw)) return new Map();
    return new Map(
      raw.filter(
        (e): e is [string, number] =>
          Array.isArray(e) && typeof e[0] === "string" && typeof e[1] === "number",
      ),
    );
  } catch {
    return new Map();
  }
}

function save(storage: Storage | undefined) {
  try {
    storage?.setItem(STORE, JSON.stringify([...positions].slice(-STORE_LIMIT)));
  } catch {
    // Private mode or storage full: Back still works, it just starts at the top.
  }
}

export function installHistoryDepth(win: Window = window): void {
  if (installed) return;
  installed = true;
  const history = win.history;
  let storage: Storage | undefined;
  try {
    storage = win.sessionStorage;
  } catch {
    storage = undefined;
  }
  positions = load(storage);
  current = depthOf(history.state);
  currentKey = keyOf(history.state) || newKey();

  const record = () => {
    positions.delete(currentKey);
    positions.set(currentKey, Math.round(win.scrollY));
  };

  // Puts an entry's position back once the page can scroll that far; stops early if the person
  // scrolls, taps or types first, so it never fights them.
  let cancelRestore = () => {};
  const restore = (key: string) => {
    cancelRestore();
    const target = positions.get(key) ?? 0;
    const doc = win.document?.documentElement;
    if (!doc || target <= 0) return;
    const until = Date.now() + RESTORE_FOR_MS;
    let frame = 0;
    const stop = () => {
      win.cancelAnimationFrame(frame);
      for (const type of ["wheel", "touchstart", "keydown", "pointerdown"]) {
        win.removeEventListener(type, stop);
      }
    };
    for (const type of ["wheel", "touchstart", "keydown", "pointerdown"]) {
      win.addEventListener(type, stop, { passive: true });
    }
    const step = () => {
      if (doc.scrollHeight - win.innerHeight >= target) {
        win.scrollTo(0, target);
        stop();
      } else if (Date.now() < until) {
        frame = win.requestAnimationFrame(step);
      } else {
        stop();
      }
    };
    step();
    cancelRestore = stop;
  };

  const push = history.pushState.bind(history);
  const replace = history.replaceState.bind(history);
  history.pushState = (state: unknown, unused: string, url?: string | URL | null) => {
    record();
    save(storage);
    cancelRestore();
    current += 1;
    currentKey = newKey();
    push(tagged(state, current, currentKey), unused, url);
  };
  history.replaceState = (state: unknown, unused: string, url?: string | URL | null) => {
    replace(tagged(state, current, currentKey), unused, url);
  };
  // The browser handles the first try; it's often too early, so the position is put back here.
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";

  let scrollFrame = 0;
  win.addEventListener(
    "scroll",
    () => {
      if (scrollFrame) return;
      scrollFrame = win.requestAnimationFrame(() => {
        scrollFrame = 0;
        record();
      });
    },
    { passive: true },
  );
  win.addEventListener("pagehide", () => {
    record();
    save(storage);
  });
  win.addEventListener("popstate", (event) => {
    current = depthOf(event.state);
    currentKey = keyOf(event.state) || newKey();
    restore(currentKey);
  });
  // Tag the entry this page loaded into, without changing its depth; after a reload, put back
  // where it was.
  history.replaceState(history.state, "");
  restore(currentKey);
}

/** True when the entry behind this one is an in-app page this tab came from. */
export function canGoBackInApp(): boolean {
  return installed && current > 0;
}

/** For tests only. */
export function resetHistoryDepth(): void {
  installed = false;
  current = 0;
  currentKey = "";
  positions = new Map();
}
