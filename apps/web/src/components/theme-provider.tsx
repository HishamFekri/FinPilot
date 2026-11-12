"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

/** The nonce lets next-themes' small inline script (which sets the theme before paint, so there's
 *  no flash) pass the Content Security Policy. */
export function ThemeProvider({ children, nonce }: { children: ReactNode; nonce?: string }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      storageKey="fp_theme"
      nonce={nonce}
    >
      {children}
    </NextThemesProvider>
  );
}
