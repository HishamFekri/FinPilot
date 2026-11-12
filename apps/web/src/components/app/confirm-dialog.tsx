"use client";

import { AlertDialog } from "@base-ui/react/alert-dialog";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import appStyles from "./app.module.css";
import { useOptionalPortalRoot } from "./portal-root";
import { Button, Notice } from "./ui";

/**
 * FinPilot's one confirmation: a title, what will and won't happen, and two buttons. The action
 * runs only on confirm; if it fails, the dialog stays open with the reason (never a false
 * success), so nothing is claimed that the server refused.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  body,
  confirm,
  tone = "danger",
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  body: ReactNode;
  confirm: string;
  tone?: "danger" | "primary";
  /** Resolves on success; throws (or returns a message) on failure. */
  onConfirm: () => Promise<string | null | void>;
}) {
  const t = useTranslations("app.confirm");
  const root = useOptionalPortalRoot();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setFailure(null);
    try {
      const problem = await onConfirm();
      if (problem) setFailure(problem);
      else onOpenChange(false);
    } catch {
      setFailure(t("failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AlertDialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) setFailure(null);
        onOpenChange(next);
      }}
    >
      <AlertDialog.Portal container={root ?? undefined}>
        <AlertDialog.Backdrop className={appStyles.dialogBackdrop} />
        <AlertDialog.Popup className={appStyles.dialog}>
          <AlertDialog.Title className={appStyles.dialogTitle}>{title}</AlertDialog.Title>
          <AlertDialog.Description className={appStyles.dialogBody} render={<div />}>
            {body}
          </AlertDialog.Description>
          {failure ? <Notice tone="error">{failure}</Notice> : null}
          <div className={appStyles.dialogActions}>
            <AlertDialog.Close className={`${appStyles.btn} ${appStyles.btnSecondary}`}>
              {t("cancel")}
            </AlertDialog.Close>
            <Button variant={tone} busy={busy} onClick={() => void run()}>
              {confirm}
            </Button>
          </div>
        </AlertDialog.Popup>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

type Ask = { title: ReactNode; body?: ReactNode; confirm?: string; tone?: "danger" | "primary" };

/**
 * The same confirmation as a question that can be awaited: `if (await ask({...})) act()`. Render
 * the returned element once in the component. Replaces the browser's own confirm everywhere.
 */
export function useConfirm(): [ReactNode, (question: Ask) => Promise<boolean>] {
  const t = useTranslations("app.confirm");
  const root = useOptionalPortalRoot();
  const [question, setQuestion] = useState<(Ask & { resolve: (yes: boolean) => void }) | null>(
    null,
  );
  const answer = (yes: boolean) => {
    question?.resolve(yes);
    setQuestion(null);
  };
  const element = (
    <AlertDialog.Root open={question !== null} onOpenChange={(open) => !open && answer(false)}>
      <AlertDialog.Portal container={root ?? undefined}>
        <AlertDialog.Backdrop className={appStyles.dialogBackdrop} />
        <AlertDialog.Popup className={appStyles.dialog}>
          <AlertDialog.Title className={appStyles.dialogTitle}>{question?.title}</AlertDialog.Title>
          <AlertDialog.Description className={appStyles.dialogBody} render={<div />}>
            {question?.body}
          </AlertDialog.Description>
          <div className={appStyles.dialogActions}>
            <AlertDialog.Close className={`${appStyles.btn} ${appStyles.btnSecondary}`}>
              {t("cancel")}
            </AlertDialog.Close>
            <Button variant={question?.tone ?? "danger"} onClick={() => answer(true)}>
              {question?.confirm ?? t("proceed")}
            </Button>
          </div>
        </AlertDialog.Popup>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
  return [element, (q) => new Promise((resolve) => setQuestion({ ...q, resolve }))];
}
