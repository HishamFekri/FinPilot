import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { AccountsView } from "@/components/app/accounts/accounts-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.accounts") }) };
}

export default async function AccountsPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <AccountsView />;
}
