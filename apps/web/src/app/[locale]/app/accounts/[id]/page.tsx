import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { AccountDetailView } from "@/components/app/accounts/account-detail-view";
import type { Locale } from "@/i18n/routing";
import { isUuid } from "@/lib/app-routes";

type Props = { params: Promise<{ locale: Locale; id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.accounts") }) };
}

// The account itself is loaded in the browser, after the session check, so no account data is
// ever in the server-rendered HTML.
export default async function AccountPage({ params }: Props) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  if (!isUuid(id)) notFound();
  return <AccountDetailView accountId={id} />;
}
