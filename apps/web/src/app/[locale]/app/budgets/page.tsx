import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { BudgetsView } from "@/components/app/analytics/budgets-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.budgets") }) };
}

export default async function BudgetsPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <BudgetsView />;
}
