import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { Suspense } from "react";
import { AnalyticsView } from "@/components/app/analytics/analytics-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.analytics") }) };
}

/** Insights and Reports, one page with tabs (?tab=). The old addresses forward here. */
export default async function AnalyticsPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return (
    <Suspense>
      <AnalyticsView />
    </Suspense>
  );
}
