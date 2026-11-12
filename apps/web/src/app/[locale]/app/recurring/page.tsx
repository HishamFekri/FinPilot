import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { RecurringView } from "@/components/app/recurring/recurring-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.recurring") }) };
}

export default async function RecurringPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <RecurringView />;
}
