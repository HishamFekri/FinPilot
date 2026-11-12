import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { CalendarView } from "@/components/app/calendar/calendar-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.calendar") }) };
}

export default async function CalendarPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <CalendarView />;
}
