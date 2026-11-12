"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type Status = "checking" | "ok" | "unreachable";

const TIMEOUT_MS = 5000;

export function ApiStatus() {
  const t = useTranslations("status");
  const [status, setStatus] = useState<Status>("checking");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let stale = false;

    // `attempt` is only a trigger: bumping it re-runs this effect, so "Check again" really refetches.
    fetch("/api/v1/healthz", { signal: AbortSignal.timeout(TIMEOUT_MS), cache: "no-store" })
      .then((response) => {
        if (!stale) setStatus(response.ok ? "ok" : "unreachable");
      })
      .catch(() => {
        if (!stale) setStatus("unreachable");
      });

    return () => {
      stale = true;
    };
  }, [attempt]);

  return (
    <section className="rounded-lg border p-4" aria-labelledby="api-status-heading">
      <h2 id="api-status-heading" className="font-medium">
        {t("heading")}
      </h2>
      <p role="status" className="mt-1 text-sm text-muted-foreground">
        {t(status)}
      </p>
      {status === "unreachable" && (
        <Button
          className="mt-3"
          variant="outline"
          size="sm"
          onClick={() => {
            setStatus("checking");
            setAttempt((value) => value + 1);
          }}
        >
          {t("retry")}
        </Button>
      )}
    </section>
  );
}
