import type { Metadata } from "next";
import { headers } from "next/headers";
import { getTranslations } from "next-intl/server";
import { ThemeProvider } from "@/components/theme-provider";
import { getDirection, localeFromHeader } from "@/i18n/routing";
import { fontVariables } from "@/lib/fonts";
import "./globals.css";

/**
 * The 404 page for any URL that matches no route. Throwing notFound() from a catch-all page
 * makes Next.js send a bare <html id="__next_error__"> shell, without lang or dir, and rebuild
 * the page on the client. An unmatched route is server-rendered as a complete document instead.
 *
 * This file bypasses layouts, so it brings its own styles, fonts, and theme.
 */
async function requestLocale() {
  return localeFromHeader((await headers()).get("X-NEXT-INTL-LOCALE"));
}

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations({ locale: await requestLocale(), namespace: "errors" });
  return { title: t("notFoundTitle") };
}

export default async function GlobalNotFound() {
  const locale = await requestLocale();
  // The proxy's CSP nonce, for the theme script (see the locale layout).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  const t = await getTranslations({ locale, namespace: "errors" });

  return (
    // The theme script changes <html> before hydration, which React would otherwise flag.
    <html
      lang={locale}
      dir={getDirection(locale)}
      className={fontVariables}
      suppressHydrationWarning
    >
      <body className="min-h-dvh bg-background text-foreground antialiased">
        <ThemeProvider nonce={nonce}>
          <main className="mx-auto max-w-4xl space-y-3 px-4 py-8">
            <h1 className="text-xl font-semibold">{t("notFoundTitle")}</h1>
            <p className="text-muted-foreground">{t("notFoundDescription")}</p>
            {/* A plain link, since this page sits outside the app's router. */}
            <a href={`/${locale}`} className="underline underline-offset-4">
              {t("home")}
            </a>
          </main>
        </ThemeProvider>
      </body>
    </html>
  );
}
