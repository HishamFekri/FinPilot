import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import proxy from "./proxy";

const request = (url: string, headers: Record<string, string> = {}) =>
  new NextRequest(url, { headers: { host: new URL(url).host, ...headers } });

describe("proxy", () => {
  it("sends app routes without a session cookie to the landing page, remembering where they were", () => {
    const response = proxy(request("http://localhost:3000/ar/app/settings"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "http://localhost:3000/ar?next=%2Far%2Fapp%2Fsettings",
    );
    expect(response.headers.get("cache-control")).toContain("no-store");
  });

  it("sends the app's home to the plain landing page", () => {
    for (const locale of ["en", "ar", "tr"]) {
      const response = proxy(request(`http://localhost:3000/${locale}/app`));
      expect(response.headers.get("location")).toBe(`http://localhost:3000/${locale}`);
    }
  });

  it("redirects the old sign-in URL to the landing page in the same language", () => {
    for (const locale of ["en", "ar", "tr"]) {
      for (const path of ["sign-in", "sign-in/"]) {
        const response = proxy(request(`http://localhost:3000/${locale}/${path}`));
        expect(response.status).toBe(307);
        expect(response.headers.get("location")).toBe(`http://localhost:3000/${locale}`);
        expect(response.headers.get("cache-control")).toContain("no-store");
      }
    }
  });

  it("keeps only the old URL's values it can vouch for", () => {
    const location = (query: string) =>
      proxy(request(`http://localhost:3000/ar/sign-in?${query}`)).headers.get("location");
    expect(location("error=cancelled")).toBe("http://localhost:3000/ar?error=cancelled");
    expect(location("notice=expired&next=%2Far%2Fapp%2Fsettings")).toBe(
      "http://localhost:3000/ar?next=%2Far%2Fapp%2Fsettings&notice=expired",
    );
    // Nothing from the URL is passed along as text, and no other site can be the destination.
    expect(location("error=%3Cscript%3E")).toBe("http://localhost:3000/ar?error=failed");
    expect(location("next=https%3A%2F%2Fevil.example")).toBe("http://localhost:3000/ar");
    expect(location("next=%2F%2Fevil.example")).toBe("http://localhost:3000/ar");
    expect(location("notice=signed_out")).toBe("http://localhost:3000/ar");
    expect(location("utm=anything")).toBe("http://localhost:3000/ar");
  });

  it("lets a request with a session cookie through, with a fresh nonce each time", () => {
    const first = proxy(request("http://localhost:3000/en/app", { cookie: "fp_session=x" }));
    const second = proxy(request("http://localhost:3000/en/app", { cookie: "fp_session=x" }));
    const nonce = (r: Response) =>
      /'nonce-([^']+)'/.exec(r.headers.get("content-security-policy") ?? "")?.[1];
    expect(first.headers.get("location")).toBeNull();
    expect(nonce(first)).toBeTruthy();
    expect(nonce(first)).not.toBe(nonce(second));
    expect(first.headers.get("cache-control")).toContain("no-store");
    expect(first.headers.get("referrer-policy")).toBe("no-referrer");
  });

  it("leaves public pages cacheable, with the CSP and a normal referrer policy", () => {
    const response = proxy(request("http://localhost:3000/en"));
    expect(response.headers.get("content-security-policy")).toContain("frame-ancestors 'none'");
    expect(response.headers.get("referrer-policy")).toBe("strict-origin-when-cross-origin");
    expect(response.headers.get("cache-control")).toBeNull();
  });

  it("answers an unknown app page with a 404 before rendering, signed in or not", () => {
    for (const cookie of ["fp_session=x", ""]) {
      const response = proxy(request("http://localhost:3000/en/app/nope", { cookie }));
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.get("x-middleware-rewrite")).toBe(
        "http://localhost:3000/en/page-not-found",
      );
    }
  });

  it("knows every real app page", () => {
    for (const path of [
      "/en/app",
      "/ar/app/settings",
      "/tr/app/settings/sessions",
      "/ar/app/settings/privacy",
      "/en/app/trips/",
      "/en/app/accounts",
      "/ar/app/accounts/0b7f8e8e-2d4a-4c1a-9a55-1f7f7e3b9c21",
      "/en/app/transactions",
      "/ar/app/recurring",
      "/tr/app/settings/categories",
    ]) {
      const response = proxy(request(`http://localhost:3000${path}`, { cookie: "fp_session=x" }));
      expect(response.headers.get("x-middleware-rewrite") ?? "").not.toContain("page-not-found");
    }
  });

  it("treats only a UUID as an account page", () => {
    for (const path of ["/en/app/accounts/new", "/en/app/accounts/1", "/en/app/accounts/x/y"]) {
      const response = proxy(request(`http://localhost:3000${path}`, { cookie: "fp_session=x" }));
      expect(response.headers.get("x-middleware-rewrite")).toBe(
        "http://localhost:3000/en/page-not-found",
      );
    }
  });

  it("moves development from 127.0.0.1 to localhost, where the sign-in cookies live", () => {
    const response = proxy(request("http://127.0.0.1:3000/en?next=%2Fen%2Fapp%2Fgoals"));
    expect(response.headers.get("location")).toBe(
      "http://localhost:3000/en?next=%2Fen%2Fapp%2Fgoals",
    );
  });
});
