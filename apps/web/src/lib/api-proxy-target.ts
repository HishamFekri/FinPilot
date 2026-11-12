const DEFAULT_TARGET = "http://127.0.0.1:8000";

/** Validates API_PROXY_TARGET at startup, so a bad value fails loudly rather than per request. */
export function resolveApiProxyTarget(value: string | undefined): string {
  const url = new URL(value || DEFAULT_TARGET);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("API_PROXY_TARGET must be an http or https URL");
  }
  return url.origin;
}
