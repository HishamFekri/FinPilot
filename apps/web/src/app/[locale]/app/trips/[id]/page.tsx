import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { TripView } from "@/components/app/trips/trip-view";
import type { Locale } from "@/i18n/routing";
import { isUuid } from "@/lib/app-routes";

type Props = { params: Promise<{ locale: Locale; id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.trips") }) };
}

export default async function TripPage({ params }: Props) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  if (!isUuid(id)) notFound();
  return <TripView id={id} />;
}
