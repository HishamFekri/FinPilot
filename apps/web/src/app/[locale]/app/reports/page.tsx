import { permanentRedirect } from "next/navigation";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

/** Reports (with its exports) is the Reports tab of Analytics now; old links land there. */
export default async function ReportsPage({ params }: Props) {
  const { locale } = await params;
  permanentRedirect(`/${locale}/app/analytics?tab=reports`);
}
