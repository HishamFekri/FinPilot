import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LanguageSwitcher } from "@/components/language-switcher";
import { ThemeToggle } from "@/components/theme-toggle";
import { Link } from "@/i18n/navigation";

/** Plain chrome for utility pages (errors, not found). The landing page draws its own. */
export function SiteShell({ children }: { children: ReactNode }) {
  const t = useTranslations("shell");

  return (
    <>
      <header className="border-b">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <Link href="/" className="font-semibold">
            {t("brand")}
          </Link>
          <div className="flex items-center gap-2">
            <LanguageSwitcher />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-8">{children}</main>
    </>
  );
}
