"use client";

import { Dialog } from "@base-ui/react/dialog";
import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useId } from "react";
import appStyles from "../app.module.css";
import { usePortalRoot } from "../app-shell";
import { cn } from "@/lib/utils";
import s from "./finance.module.css";
import { useFinanceState } from "./finance-provider";
import { QuickAdd } from "./quick-add";
import { TemplateStrip } from "./templates";

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
    target.closest("[role=dialog], [role=menu], [role=listbox]") !== null
  );
}

/** New transaction, wherever the page puts it. The N shortcut lives in the dialog below. */
export function AddButton({
  variant = "plain",
  className,
}: {
  variant?: "plain" | "sidebar" | "header";
  className?: string;
}) {
  const t = useTranslations("app.quickAdd");
  const state = useFinanceState();
  const ready = state?.load.kind === "ready";
  const setOpen = state?.quickAdd.setOpen;

  if (!ready || !setOpen) return null;
  return (
    <button
      type="button"
      className={cn(s.addButton, variant === "header" && s.addHeader, className)}
      onClick={() => setOpen(true)}
      aria-keyshortcuts="N"
      title={t("openShortcut")}
    >
      <Plus aria-hidden="true" />
      <span className={s.addLabel}>{t("open")}</span>
      {variant === "sidebar" ? (
        <kbd className={s.kbd} aria-hidden="true">
          {"N"}
        </kbd>
      ) : null}
    </button>
  );
}

export function QuickAddDialog() {
  const t = useTranslations("app.quickAdd");
  const root = usePortalRoot();
  const state = useFinanceState();
  const amountId = useId();
  const ready = state?.load.kind === "ready";
  const setOpen = state?.quickAdd.setOpen;

  // N opens this from anywhere, unless the person is typing or another dialog is open.
  useEffect(() => {
    if (!ready || !setOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== "n" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.repeat || typing(event.target) || document.querySelector("[role=dialog]")) return;
      event.preventDefault();
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready, setOpen]);

  if (!state || state.load.kind !== "ready") return null;
  const { open } = state.quickAdd;

  return (
    <Dialog.Root open={open} onOpenChange={state.quickAdd.setOpen}>
      <Dialog.Portal container={root}>
        <Dialog.Backdrop className={appStyles.dialogBackdrop} />
        <Dialog.Popup
          className={s.quickDialog}
          initialFocus={() => document.getElementById(amountId)}
        >
          <Dialog.Title className={s.quickDialogTitle}>{t("dialogTitle")}</Dialog.Title>
          <TemplateStrip limit={4} />
          <QuickAdd
            key={state.quickAdd.preset?.n ?? "plain"}
            mode="dialog"
            amountId={amountId}
            preset={state.quickAdd.preset}
            onClose={() => state.quickAdd.setOpen(false)}
          />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
