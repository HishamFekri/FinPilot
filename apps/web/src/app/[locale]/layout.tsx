import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { hasLocale, NextIntlClientProvider } from "next-intl";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { headers } from "next/headers";
import type { ReactNode } from "react";
import { ThemeProvider } from "@/components/theme-provider";
import { getDirection, routing } from "@/i18n/routing";
import { fontVariables } from "@/lib/fonts";
import "../globals.css";

type Props = {
  children: ReactNode;
  params: Promise<{ locale: string }>;
};

// Anything other than these locales, such as /file.png, is an unmatched route, and goes to
// app/global-not-found.tsx rather than reaching the notFound() call below.
export const dynamicParams = false;

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export async function generateMetadata({ params }: Omit<Props, "children">): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: "metadata" });
  return { title: t("title"), description: t("description") };
}

export default async function LocaleLayout({ children, params }: Props) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  // The proxy's per-response CSP nonce. Reading it makes every page dynamically rendered, which
  // a nonce requires: a static page would carry one nonce for everyone.
  const nonce = (await headers()).get("x-nonce") ?? undefined;

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
          {/* Each page draws its own chrome: the landing page is full-bleed, and utility pages
              use SiteShell. */}
          <NextIntlClientProvider>{children}</NextIntlClientProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
