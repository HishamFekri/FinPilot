import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppShell } from "@/components/app/app-shell";

// Private pages: never indexed. The shell renders nothing personal until the API has confirmed
// the session in the browser, so the server-rendered HTML holds no account data.
export const metadata: Metadata = { robots: { index: false, follow: false } };

export default function AppLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
