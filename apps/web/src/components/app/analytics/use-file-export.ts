"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import type { Locale } from "@/i18n/routing";
import { localeTag } from "@/lib/format";

/**
 * Downloads and prints an export the API makes, as the Reports page does: errors are explained
 * (rate limit, too large, PDF busy or unavailable), a PDF reports its own completeness from the
 * API's headers, and Print opens that same PDF (never what the browser happens to show).
 */
export function useFileExport(labels?: {
  complete: (total: string) => string;
  partial: (included: string, total: string) => string;
}) {
  const t = useTranslations("app.reports");
  const locale = useLocale() as Locale;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ partial: boolean; text: string } | null>(null);
  const number = (n: number) => new Intl.NumberFormat(localeTag(locale)).format(n);

  const failure = async (response: Response): Promise<string> => {
    const body = (await response.json().catch(() => null)) as { code?: string } | null;
    const code = body?.code;
    return response.status === 429
      ? t("export.rateLimited")
      : code === "export_too_large"
        ? t("export.tooLarge")
        : code === "pdf_busy"
          ? t("export.busy")
          : code === "pdf_unavailable"
            ? t("export.pdfUnavailable")
            : t("export.failed");
  };

  const completeness = (response: Response) => {
    const included = Number(response.headers.get("X-Report-Rows-Included") ?? 0);
    const total = Number(response.headers.get("X-Report-Rows-Total") ?? included);
    const partial = response.headers.get("X-Report-Complete") === "false";
    setDone({
      partial,
      text: partial
        ? (labels?.partial(number(included), number(total)) ??
          t("export.partialDone", { included: number(included), total: number(total) }))
        : (labels?.complete(number(total)) ?? t("export.completeDone", { total: number(total) })),
    });
  };

  const download = async (url: string, name: string, isPdf: boolean) => {
    setBusy(url);
    setError(null);
    setDone(null);
    try {
      const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) {
        setError(await failure(response));
        return;
      }
      if (isPdf) completeness(response);
      const blob = await response.blob();
      const file =
        /filename="?([^";]+)"?/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ??
        name;
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = file;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch {
      setError(t("export.failed"));
    } finally {
      setBusy(null);
    }
  };

  const print = async (pdfUrl: string) => {
    setError(null);
    setDone(null);
    const win = window.open("", "_blank");
    if (!win) {
      setError(t("export.popupBlocked"));
      return;
    }
    win.document.title = t("export.printWindow");
    win.document.body.textContent = t("export.printing");
    setBusy("print");
    try {
      const response = await fetch(pdfUrl, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) {
        win.close();
        setError(await failure(response));
        return;
      }
      completeness(response);
      const href = URL.createObjectURL(
        new Blob([await response.blob()], { type: "application/pdf" }),
      );
      win.location.href = href;
      window.setTimeout(() => URL.revokeObjectURL(href), 120_000);
    } catch {
      win.close();
      setError(t("export.failed"));
    } finally {
      setBusy(null);
    }
  };

  return { busy, error, done, download, print };
}
