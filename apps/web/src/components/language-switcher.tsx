"use client";

import { useLocale, useTranslations } from "next-intl";
import { getPathname, usePathname } from "@/i18n/navigation";
import { localeNames, routing } from "@/i18n/routing";
import { cn } from "@/lib/utils";

export function LanguageSwitcher() {
  const t = useTranslations("shell");
  const current = useLocale();
  const pathname = usePathname();

  return (
    <nav aria-label={t("languageLabel")}>
      <ul className="flex items-center gap-1">
        {routing.locales.map((locale) => (
          <li key={locale}>
            {/* A full page load on purpose. <html> lives in the locale layout, and its lang, dir,
                and fonts all change, so client navigation would rebuild the whole document. */}
            <a
              href={getPathname({ href: pathname, locale })}
              hrefLang={locale}
              lang={locale}
              aria-current={locale === current ? "true" : undefined}
              className={cn(
                "inline-flex min-h-9 items-center rounded-md px-2 text-sm hover:bg-muted",
                locale === current && "font-semibold underline underline-offset-4",
              )}
            >
              {localeNames[locale]}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
