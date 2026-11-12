import { describe, expect, it } from "vitest";
import {
  googleStartHref,
  initials,
  safeReturnPath,
  signInError,
  landingHref,
  signInNotice,
} from "./auth";
import { buildCsp } from "./csp";

describe("safeReturnPath", () => {
  it.each(["/en/app", "/ar/app/settings", "/tr/app/settings/sessions", "/en/app/accounts/"])(
    "accepts the app path %s",
    (path) => {
      expect(safeReturnPath(path)).toBe(path);
    },
  );

  it.each([
    "https://evil.example/en/app",
    "//evil.example/en/app",
    "/\\evil.example",
    "/en/app/../../admin",
    "/en/app/./settings",
    "/en/app?x=1",
    "/en/app#top",
    "/en/app/%2e%2e",
    "/en",
    "/xx/app",
    "/en/sign-in",
    "javascript:alert(1)",
    "",
    null,
    undefined,
    `/en/app/${"a".repeat(200)}`,
  ])("refuses %s", (path) => {
    expect(safeReturnPath(path)).toBeNull();
  });
});

describe("sign-in query values", () => {
  it("passes the API's fixed error codes through, and nothing else", () => {
    expect(signInError("cancelled")).toBe("cancelled");
    expect(signInError("google_unavailable")).toBe("google_unavailable");
    expect(signInError("unreachable")).toBe("unreachable");
    expect(signInError("<script>alert(1)</script>")).toBe("failed");
    expect(signInError("access_denied")).toBe("failed");
    expect(signInError(null)).toBeNull();
  });

  it("accepts only the web app's own notices", () => {
    expect(signInNotice("signed_out")).toBeNull();
    expect(signInNotice("expired")).toBe("expired");
    expect(signInNotice("anything")).toBeNull();
  });

  it("builds the landing page link, dropping an unsafe destination", () => {
    expect(landingHref("ar", { next: "/ar/app/settings", notice: "expired" })).toBe(
      "/ar?next=%2Far%2Fapp%2Fsettings&notice=expired",
    );
    expect(landingHref("en", { next: "https://evil.example" })).toBe("/en");
    expect(landingHref("en", { next: "/en/app" })).toBe("/en");
    expect(landingHref("tr", { error: "cancelled" })).toBe("/tr?error=cancelled");
  });
});

describe("googleStartHref", () => {
  it("starts the real backend flow and returns to the app", () => {
    expect(googleStartHref("en")).toBe("/api/v1/auth/google/start?locale=en&return_to=%2Fen%2Fapp");
  });

  it("keeps the destination, in the language being signed in with", () => {
    expect(googleStartHref("tr", "/ar/app/settings")).toBe(
      "/api/v1/auth/google/start?locale=tr&return_to=%2Ftr%2Fapp%2Fsettings",
    );
  });

  it("never forwards an unsafe destination", () => {
    expect(googleStartHref("en", "//evil.example")).toBe(
      "/api/v1/auth/google/start?locale=en&return_to=%2Fen%2Fapp",
    );
  });
});

describe("initials", () => {
  it.each([
    ["Hisham Jouda", null, "HJ"],
    ["Ada", null, "AD"],
    [null, "zeynep.kaya@example.com", "ZK"],
    ["هشام جودة", null, "هج"],
    [null, null, "?"],
  ])("%s / %s → %s", (name, email, expected) => {
    expect(initials(name, email)).toBe(expected);
  });
});

describe("Content Security Policy", () => {
  const production = buildCsp("abc123", { dev: false });

  it("allows scripts only by this response's nonce in production", () => {
    const scripts = production.split("; ").find((d) => d.startsWith("script-src "));
    expect(scripts).toBe("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(production).not.toContain("unsafe-eval");
    expect(production).not.toMatch(/(script|style)-src [^;]*'unsafe-inline'/);
  });

  it("forbids framing, plugins, and other base URLs", () => {
    expect(production).toContain("frame-ancestors 'none'");
    expect(production).toContain("object-src 'none'");
    expect(production).toContain("base-uri 'self'");
    expect(production).toContain("connect-src 'self'");
  });

  it("loosens only what development tooling needs", () => {
    const dev = buildCsp("abc123", { dev: true });
    expect(dev).toContain("'unsafe-eval'");
    expect(dev).not.toContain("upgrade-insecure-requests");
  });
});
