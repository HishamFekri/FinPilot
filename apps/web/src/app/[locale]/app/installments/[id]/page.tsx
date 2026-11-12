import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { PlanView } from "@/components/app/installments/plan-view";
import type { Locale } from "@/i18n/routing";
import { isUuid } from "@/lib/app-routes";

type Props = { params: Promise<{ locale: Locale; id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.installments") }) };
}

export default async function InstallmentPlanPage({ params }: Props) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  if (!isUuid(id)) notFound();
  return <PlanView id={id} />;
}
