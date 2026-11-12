import type { Metadata } from "next";
import { setRequestLocale } from "next-intl/server";
import { InviteRedirect } from "@/components/app/trips/invite-redirect";
import type { Locale } from "@/i18n/routing";

type Props = { params: Promise<{ locale: Locale }> };

// The link's token is in the fragment; nothing about it reaches the server or search engines.
export const metadata: Metadata = { robots: { index: false, follow: false } };

export default async function InvitePage({ params }: Props) {
  setRequestLocale((await params).locale);
  return <InviteRedirect />;
}
