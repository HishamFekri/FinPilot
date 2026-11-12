/**
 * The one way the browser talks to the FinPilot API.
 *
 * - Same origin only: `/api/v1/...` goes through the web app's rewrite to the API, so cookies are
 *   first-party and no CORS is needed (ARCHITECTURE.md §1).
 * - The session cookie is HttpOnly. This code never sees it, and never stores a token anywhere.
 * - Unsafe methods send `X-CSRF-Token`, copied from the readable `fp_csrf` cookie (SECURITY.md §3).
 * - Failures are classified, so a network blip is never mistaken for being signed out.
 */

export const API_BASE = "/api/v1";
const TIMEOUT_MS = 10_000;
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// Production uses the __Host- prefix (https only); local development can't.
const CSRF_COOKIES = ["__Host-fp_csrf", "fp_csrf"];

export type ApiErrorKind =
  /** 401: the session is missing, expired, or revoked. A confirmed answer from the API. */
  | "unauthenticated"
  /** 403 account_unavailable: suspended or deleted. The API has already ended the session. */
  | "account_unavailable"
  /** 403 csrf_failed: the request couldn't be verified. Usually a stale tab after sign-out. */
  | "csrf_failed"
  /** 422: the API refused the input. `fields` names the fields when it says which. */
  | "validation"
  /** 429 */
  | "rate_limited"
  /** 404 */
  | "not_found"
  /** No answer from the API: offline, timed out, or the API (or its proxy) is down. */
  | "unavailable"
  /** Anything else the API answered with. */
  | "server";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number,
    /** The API's stable code. Shown to people only through a translation, never verbatim. */
    readonly code: string | null = null,
    readonly fields: readonly string[] = [],
  ) {
    super(`${kind}${code ? `: ${code}` : ""}`);
    this.name = "ApiError";
  }
}

export function readCsrfToken(cookieString: string = document.cookie): string | null {
  for (const part of cookieString.split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    const name = part.slice(0, index).trim();
    if (CSRF_COOKIES.includes(name)) {
      const value = part.slice(index + 1).trim();
      return value ? decodeURIComponent(value) : null;
    }
  }
  return null;
}

type Problem = { code?: unknown; errors?: unknown };

function isProblem(response: Response): boolean {
  const type = response.headers.get("content-type") ?? "";
  return type.includes("application/problem+json") || type.includes("application/json");
}

async function toError(response: Response): Promise<ApiError> {
  // A proxy in front of a stopped API answers with its own 5xx page, not a problem document.
  if (!isProblem(response)) {
    const down = response.status >= 500;
    return new ApiError(down ? "unavailable" : "server", response.status);
  }
  let body: Problem = {};
  try {
    body = (await response.json()) as Problem;
  } catch {
    return new ApiError(response.status >= 500 ? "unavailable" : "server", response.status);
  }
  const code = typeof body.code === "string" ? body.code : null;
  const fields = Array.isArray(body.errors)
    ? body.errors
        .map((e: unknown) =>
          e && typeof e === "object" && "field" in e ? String((e as { field: unknown }).field) : "",
        )
        // "body.display_name" → "display_name"
        .map((f) => f.split(".").pop() ?? "")
        .filter(Boolean)
    : [];

  switch (response.status) {
    case 401:
      return new ApiError("unauthenticated", 401, code);
    case 403:
      if (code === "account_unavailable") return new ApiError("account_unavailable", 403, code);
      if (code === "csrf_failed") return new ApiError("csrf_failed", 403, code);
      return new ApiError("server", 403, code);
    case 404:
      return new ApiError("not_found", 404, code);
    case 422:
      return new ApiError("validation", 422, code, fields);
    case 429:
      return new ApiError("rate_limited", 429, code);
    case 502:
    case 503:
    case 504:
      return new ApiError("unavailable", response.status, code);
    default:
      return new ApiError("server", response.status, code);
  }
}

export type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Extra headers, such as `Idempotency-Key` or `If-Match`. */
  headers?: Record<string, string>;
};

/** Calls the API and returns its JSON body (or undefined for 204). Throws ApiError otherwise. */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method)) {
    const token = readCsrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }

  const timeout = AbortSignal.timeout(TIMEOUT_MS);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal,
    });
  } catch (error) {
    // The caller's own cancellation is passed through untouched; everything else is "no answer".
    if (options.signal?.aborted) throw error;
    throw new ApiError("unavailable", 0);
  }

  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Whether the API answers at all. Used before leaving for Google, so a stopped API shows a
 *  friendly message instead of a raw error page. */
export async function apiReachable(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/healthz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    return response.ok;
  } catch {
    return false;
  }
}
