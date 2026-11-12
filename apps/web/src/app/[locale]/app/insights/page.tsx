import { permanentRedirect } from "next/navigation";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

/** Insights is the Spending tab of Analytics now; old links and bookmarks land there. */
export default async function InsightsPage({ params }: Props) {
  const { locale } = await params;
  permanentRedirect(`/${locale}/app/analytics?tab=spending`);
}
