/**
 * The last answer useLoad got for each key, in this tab's memory only, so a page you come back to
 * draws at once (and the browser can put the scroll back) while a fresh answer loads. Keys carry
 * the finance revision, so nothing recorded since an answer arrived is ever hidden by it. Signing
 * out reloads the page, which empties it. No imports, so tests can reset it cheaply.
 */
const recent = new Map<string, unknown>();
const LIMIT = 80;

export function remember(key: string, data: unknown) {
  recent.delete(key);
  recent.set(key, data);
  if (recent.size > LIMIT) recent.delete(recent.keys().next().value as string);
}

export function recall<T>(key: string): T | undefined {
  return recent.get(key) as T | undefined;
}

/** For tests: start each one with nothing remembered. */
export function forgetLoads() {
  recent.clear();
}
