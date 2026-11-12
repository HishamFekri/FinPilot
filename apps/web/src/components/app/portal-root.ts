"use client";

import { createContext, type RefObject, useContext } from "react";

/** The app shell's root element. Popups render inside it, so they inherit its theme tokens. */
export const PortalRootContext = createContext<RefObject<HTMLDivElement | null> | null>(null);

/** The shell's root, or null outside the shell (then popups go to <body>). */
export function useOptionalPortalRoot(): RefObject<HTMLDivElement | null> | null {
  return useContext(PortalRootContext);
}
