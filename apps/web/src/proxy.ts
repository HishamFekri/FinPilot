import { NextRequest, NextResponse } from "next/server";
import createMiddleware from "next-intl/middleware";
import { routing } from "./i18n/routing";
import { isAppPage } from "./lib/app-routes";
import { landingHref, SESSION_COOKIES, signInError, signInNotice } from "./lib/auth";
import { buildCsp, createNonce } from "./lib/csp";

const handleI18nRouting = createMiddleware(routing);
const dev = process.env.NODE_ENV !== "production";

// /en/app, /ar/app/settings, ... The second group is the path after /app.
const APP_ROUTE = /^\/(en|ar|tr)\/app(\/.*)?$/;
// The invite page holds a token in its fragment: never cached, never sent as a referrer.
const PRIVATE_ROUTE = /^\/(en|ar|tr)\/(?:app|invite)(?:\/|$)/;
// The removed sign-in page. Old links and bookmarks still work: they go to the landing page.
const OLD_SIGN_IN = /^\/(en|ar|tr)\/sign-in\/?$/;

/**
 * Runs before every page:
 *
 * 1. Development only: 127.0.0.1 → localhost. Sign-in cookies belong to the host in the Google
 *    redirect URI (localhost), so a page on 127.0.0.1 could never see them.
 * 2. The old sign-in URL goes to the landing page, keeping only values it can vouch for.
 * 3. App routes without a session cookie go to the landing page, where everyone signs in. This is
 *    a convenience that avoids a flash of the wrong page, not security: the API checks every
 *    request (ARCHITECTURE.md §5.1).
 * 4. Language routing (next-intl), with a fresh CSP nonce passed along to rendering.
 */
export default function proxy(request: NextRequest) {
  const { nextUrl } = request;

  const host = request.headers.get("host") ?? "";
  if (dev && host.startsWith("127.0.0.1:")) {
    const url = new URL(
      nextUrl.pathname + nextUrl.search,
      `http://localhost:${host.split(":")[1]}`,
    );
    return NextResponse.redirect(url, 307);
  }

  const nonce = createNonce();
  const csp = buildCsp(nonce, { dev });
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  // Next.js reads the nonce from the request's policy, and adds it to its own scripts.
  headers.set("Content-Security-Policy", csp);

  const oldSignIn = OLD_SIGN_IN.exec(nextUrl.pathname);
  if (oldSignIn) {
    const query = nextUrl.searchParams;
    const href = landingHref(oldSignIn[1] as (typeof routing.locales)[number], {
      next: query.get("next"),
      error: signInError(query.get("error")),
      notice: signInNotice(query.get("notice")),
    });
    return withPrivateHeaders(NextResponse.redirect(new URL(href, request.url), 307));
  }

  const app = APP_ROUTE.exec(nextUrl.pathname);
  if (app) {
    // An unknown page under /app is a 404, decided here: once a page starts streaming, its
    // status can no longer change. The rewrite target matches no route, so Next.js renders its
    // localized 404 page.
    if (!isAppPage(app[2] ?? "")) {
      const missing = new URL(`/${app[1]}/page-not-found`, request.url);
      const response = NextResponse.rewrite(missing, { request: { headers } });
      response.headers.set("Content-Security-Policy", csp);
      return withPrivateHeaders(response);
    }
    if (!SESSION_COOKIES.some((name) => request.cookies.has(name))) {
      const locale = app[1] as (typeof routing.locales)[number];
      const target = new URL(landingHref(locale, { next: nextUrl.pathname }), request.url);
      return withPrivateHeaders(NextResponse.redirect(target, 307));
    }
  }

  const response = handleI18nRouting(new NextRequest(request, { headers }));
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  return PRIVATE_ROUTE.test(nextUrl.pathname) ? withPrivateHeaders(response) : response;
}

/** Account pages are never cached, never sent as a referrer, and never kept for Back. */
function withPrivateHeaders(response: NextResponse) {
  response.headers.set("Cache-Control", "private, no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

export const config = {
  // Skip API calls, Next.js internals, and files with an extension.
  matcher: "/((?!api|_next|.*\\..*).*)",
};
