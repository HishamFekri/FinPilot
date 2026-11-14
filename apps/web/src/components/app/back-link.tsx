"use client";

import type { ComponentProps, MouseEvent } from "react";
import { Link } from "@/i18n/navigation";
import { canGoBackInApp } from "@/lib/history-depth";

/**
 * An on-screen Back. When this tab reached the page from another FinPilot page, it goes back one
 * step in the browser's history, exactly like the browser's own Back (so the previous page comes
 * back with its tab and its scroll position). With no in-app page behind it (a direct link, a
 * reload of a fresh tab), it opens `href`, the page this one belongs under.
 *
 * It stays a real link to `href`, so opening it in a new tab, or without JavaScript, still works.
 */
export function BackLink({ href, onClick, ...props }: ComponentProps<typeof Link>) {
  return (
    <Link
      href={href}
      onClick={(event: MouseEvent<HTMLAnchorElement>) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        // A modified click (new tab, new window) keeps the link's own behaviour.
        if (
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) {
          return;
        }
        if (!canGoBackInApp()) return;
        event.preventDefault();
        window.history.back();
      }}
      {...props}
    />
  );
}
