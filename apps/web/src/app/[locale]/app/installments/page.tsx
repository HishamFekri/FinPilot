import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { InstallmentsView } from "@/components/app/installments/installments-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.installments") }) };
}

export default async function InstallmentsPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <InstallmentsView />;
}
