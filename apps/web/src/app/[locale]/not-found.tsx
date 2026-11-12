import { useTranslations } from "next-intl";
import { SiteShell } from "@/components/site-shell";
import { Link } from "@/i18n/navigation";

// Used when a page calls notFound(), for example for a record that doesn't exist. Unmatched URLs
// use app/global-not-found.tsx instead. On this path Next.js sends an error shell first, and the
// page is built on the client (see the note in global-not-found.tsx).
export default function NotFound() {
  const t = useTranslations("errors");

  return (
    <SiteShell>
      <div className="space-y-3">
        <h1 className="text-xl font-semibold">{t("notFoundTitle")}</h1>
        <p className="text-muted-foreground">{t("notFoundDescription")}</p>
        <Link href="/" className="underline underline-offset-4">
          {t("home")}
        </Link>
      </div>
    </SiteShell>
  );
}
