import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { useTranslations } from "next-intl";
import { SiteShell } from "@/components/site-shell";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

/** The sections, in reading order. Each has a heading and a body in every language. */
export const PRIVACY_SECTIONS = [
  "operator",
  "collected",
  "notCollected",
  "use",
  "others",
  "storage",
  "choices",
  "beta",
  "contact",
] as const;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const t = await getTranslations({ locale: (await params).locale, namespace: "legal" });
  return { title: t("title"), description: t("metaDescription") };
}

function PrivacyNotice() {
  const t = useTranslations("legal");
  return (
    <SiteShell>
      <article className="space-y-6">
        <header className="space-y-2">
          <h1 className="text-xl font-semibold">{t("title")}</h1>
          <p className="text-muted-foreground text-sm">{t("updated")}</p>
        </header>
        {/* Stated first, and plainly: this hasn't had a legal review. */}
        <p role="note" className="rounded-md border p-3 text-sm">
          {t("draft")}
        </p>
        {PRIVACY_SECTIONS.map((key) => (
          <section key={key} className="space-y-1">
            <h2 className="font-medium">{t(`sections.${key}.heading`)}</h2>
            <p className="text-muted-foreground">{t(`sections.${key}.body`)}</p>
          </section>
        ))}
        <Link href="/" className="underline underline-offset-4">
          {t("back")}
        </Link>
      </article>
    </SiteShell>
  );
}

export default async function PrivacyPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <PrivacyNotice />;
}
