import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { JoinView } from "@/components/app/trips/join-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.trips") }) };
}

export default async function JoinPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <JoinView />;
}
