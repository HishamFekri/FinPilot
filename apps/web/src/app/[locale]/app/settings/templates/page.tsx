import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { TemplatesView } from "@/components/app/finance/templates";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("templates.title") }) };
}

export default async function TemplatesPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <TemplatesView />;
}
