import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, readCsrfToken } from "./api";

function respond(status: number, body?: unknown, type = "application/problem+json") {
  // A new Response per call: a body can only be read once.
  return vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
    async () =>
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: body === undefined ? {} : { "content-type": type },
      }),
  );
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("expected the call to fail");
}

afterEach(() => {
  document.cookie = "fp_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

describe("readCsrfToken", () => {
  it("reads the development and the __Host- production cookie", () => {
    expect(readCsrfToken("a=1; fp_csrf=abc%2Bdef; b=2")).toBe("abc+def");
    expect(readCsrfToken("__Host-fp_csrf=xyz")).toBe("xyz");
    expect(readCsrfToken("fp_session=secret")).toBeNull();
    expect(readCsrfToken("")).toBeNull();
  });
});

describe("api", () => {
  it("calls the same-origin API, and sends the CSRF token only on changes", async () => {
    document.cookie = "fp_csrf=token-1; path=/";
    const fetch = respond(200, { ok: true }, "application/json");
    vi.stubGlobal("fetch", fetch);

    await api("/auth/me");
    await api("/auth/me", { method: "PATCH", body: { locale: "ar" } });

    const [getUrl, getInit] = fetch.mock.calls[0] as [string, RequestInit];
    const [, patchInit] = fetch.mock.calls[1] as [string, RequestInit];
    expect(getUrl).toBe("/api/v1/auth/me");
    expect(getInit.credentials).toBe("same-origin");
    expect(getInit.cache).toBe("no-store");
    expect(getInit.headers).not.toHaveProperty("X-CSRF-Token");
    expect(patchInit.headers).toHaveProperty("X-CSRF-Token", "token-1");
    expect(patchInit.body).toBe('{"locale":"ar"}');
  });

  it("treats 204 as success with no body", async () => {
    vi.stubGlobal("fetch", respond(204));
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });

  it("separates a confirmed sign-out from everything else", async () => {
    vi.stubGlobal("fetch", respond(401, { code: "not_authenticated" }));
    expect((await failure(api("/auth/me"))).kind).toBe("unauthenticated");

    vi.stubGlobal("fetch", respond(403, { code: "account_unavailable" }));
    expect((await failure(api("/auth/me"))).kind).toBe("account_unavailable");

    vi.stubGlobal("fetch", respond(403, { code: "csrf_failed" }));
    expect((await failure(api("/auth/me", { method: "PATCH", body: {} }))).kind).toBe(
      "csrf_failed",
    );
  });

  it("never mistakes an unreachable API for being signed out", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    expect((await failure(api("/auth/me"))).kind).toBe("unavailable");

    // What the web server's proxy answers when the API process is stopped.
    vi.stubGlobal("fetch", respond(500, "Internal Server Error", "text/plain"));
    expect((await failure(api("/auth/me"))).kind).toBe("unavailable");

    vi.stubGlobal("fetch", respond(503, { code: "google_unavailable" }));
    expect((await failure(api("/auth/me"))).kind).toBe("unavailable");
  });

  it("names the refused fields of a validation error, without their values", async () => {
    vi.stubGlobal(
      "fetch",
      respond(422, {
        code: "validation_error",
        errors: [{ field: "body.time_zone", code: "value_error" }],
      }),
    );
    const error = await failure(api("/auth/me", { method: "PATCH", body: {} }));
    expect(error.kind).toBe("validation");
    expect(error.fields).toEqual(["time_zone"]);
  });

  it("reports rate limits", async () => {
    vi.stubGlobal("fetch", respond(429, { code: "rate_limited" }));
    expect((await failure(api("/auth/me"))).kind).toBe("rate_limited");
  });
});
