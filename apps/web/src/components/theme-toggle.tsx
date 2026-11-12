"use client";

import { Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const t = useTranslations("shell");
  const { resolvedTheme, setTheme } = useTheme();

  return (
    <Button
      variant="outline"
      size="icon"
      aria-label={t("toggleTheme")}
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      {/* CSS picks the icon, so the server and client render the same markup. */}
      <Sun className="dark:hidden" aria-hidden="true" />
      <Moon className="hidden dark:block" aria-hidden="true" />
    </Button>
  );
}
