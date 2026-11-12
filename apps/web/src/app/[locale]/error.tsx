"use client";

import { useTranslations } from "next-intl";
import { SiteShell } from "@/components/site-shell";
import { Button } from "@/components/ui/button";

export default function LocaleError({ reset }: { reset: () => void }) {
  const t = useTranslations("errors");

  return (
    <SiteShell>
      <div role="alert" className="space-y-3">
        <h1 className="text-xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground">{t("description")}</p>
        <Button variant="outline" onClick={reset}>
          {t("retry")}
        </Button>
      </div>
    </SiteShell>
  );
}
