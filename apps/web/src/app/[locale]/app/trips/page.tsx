import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { TripsView } from "@/components/app/trips/trips-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.trips") }) };
}

export default async function TripsPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <TripsView />;
}
