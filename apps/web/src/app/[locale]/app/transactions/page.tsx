import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { Suspense } from "react";
import { TransactionsView } from "@/components/app/finance/transactions-view";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "app" });
  return { title: t("pageTitle", { page: t("nav.transactions") }) };
}

export default async function TransactionsPage({ params }: Props) {
  setRequestLocale((await params).locale);
  return (
    <Suspense>
      <TransactionsView />
    </Suspense>
  );
}
