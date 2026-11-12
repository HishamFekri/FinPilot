import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { TripReportView } from "@/components/app/trips/trip-report-view";
import type { Locale } from "@/i18n/routing";
import { isUuid } from "@/lib/app-routes";

type Props = { params: Promise<{ locale: Locale; id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("trips.report.title") }) };
}

export default async function TripReportPage({ params }: Props) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  if (!isUuid(id)) notFound();
  return <TripReportView id={id} />;
}
