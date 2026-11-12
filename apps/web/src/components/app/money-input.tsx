"use client";

import { useLocale, useTranslations } from "next-intl";
import {
  type ChangeEvent,
  type InputEvent,
  type InputHTMLAttributes,
  type Ref,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { Locale } from "@/i18n/routing";
import { applyEdit, MARKS, type Rejection } from "@/lib/money-input";
import s from "./app.module.css";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  /** The display text, as this field shows it ("1,234.5"). `parseAmount` reads it for the API. */
  value: string;
  onValueChange: (text: string) => void;
  allowNegative?: boolean;
  /** Called when a keystroke or paste was refused, so the field can say why. */
  onRejected?: (reason: Rejection | null) => void;
  ref?: Ref<HTMLInputElement>;
};

/**
 * The one money input. Thousands separators appear as you type, in the reader's language; the
 * caret stays put; nothing typed is ever silently reinterpreted (`lib/money-input.ts`). It shows
 * display text only: callers parse it with `parseAmount` and send the canonical string.
 */
export function MoneyInput({
  value,
  onValueChange,
  allowNegative = false,
  onRejected,
  ref,
  onBeforeInput,
  ...props
}: Props) {
  const locale = useLocale() as Locale;
  // Fields that don't show the reason themselves get it right under the input.
  const ownHint = useMoneyHint();
  const report = onRejected ?? ownHint.onRejected;
  const own = useRef<HTMLInputElement | null>(null);
  const caret = useRef<number | null>(null);
  const inputType = useRef("");

  // After React writes the new text, put the caret back where the edit left it.
  useLayoutEffect(() => {
    const element = own.current;
    if (caret.current === null || !element || document.activeElement !== element) return;
    element.setSelectionRange(caret.current, caret.current);
    caret.current = null;
  });

  const setRefs = (element: HTMLInputElement | null) => {
    own.current = element;
    if (typeof ref === "function") ref(element);
    else if (ref) (ref as { current: HTMLInputElement | null }).current = element;
  };

  const change = (event: ChangeEvent<HTMLInputElement>) => {
    const element = event.target;
    const result = applyEdit(
      value,
      element.value,
      element.selectionStart ?? element.value.length,
      locale,
      { allowNegative, inputType: inputType.current },
    );
    inputType.current = "";
    report(result.rejected);
    caret.current = result.caret;
    if (result.text === value) {
      // Nothing changes in state, so React won't re-render: restore the field directly.
      element.value = result.text;
      element.setSelectionRange(result.caret, result.caret);
      caret.current = null;
    } else {
      onValueChange(result.text);
    }
  };

  return (
    <>
      <input
        ref={setRefs}
        inputMode="decimal"
        autoComplete="off"
        spellCheck={false}
        dir="ltr"
        {...props}
        value={value}
        onBeforeInput={(event: InputEvent<HTMLInputElement>) => {
          inputType.current = event.nativeEvent.inputType ?? "";
          onBeforeInput?.(event);
        }}
        onChange={change}
      />
      {!onRejected && ownHint.hint ? (
        <span role="status" className={s.moneyHint}>
          {ownHint.hint}
        </span>
      ) : null}
    </>
  );
}

/** Why the last keystroke or paste was refused, for a few seconds, politely announced. */
export function useMoneyHint() {
  const t = useTranslations("app.money");
  const locale = useLocale() as Locale;
  const [reason, setReason] = useState<Rejection | null>(null);
  useEffect(() => {
    if (!reason) return;
    const timer = window.setTimeout(() => setReason(null), 4000);
    return () => window.clearTimeout(timer);
  }, [reason]);
  return {
    // A refused edit explains itself; the next accepted edit clears the explanation.
    onRejected: (next: Rejection | null) => setReason(next),
    hint: reason ? t(`rejected.${reason}`, { decimal: MARKS[locale].decimal }) : null,
  };
}
