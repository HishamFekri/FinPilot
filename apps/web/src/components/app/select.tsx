"use client";

import { DirectionProvider } from "@base-ui/react/direction-provider";
import { Select as BaseSelect } from "@base-ui/react/select";
import { Check, ChevronDown } from "lucide-react";
import { useLocale } from "next-intl";
import { Children, Fragment, isValidElement, type ReactElement, type ReactNode } from "react";
import { getDirection, type Locale } from "@/i18n/routing";
import { cn } from "@/lib/utils";
import { useOptionalPortalRoot } from "./portal-root";
import s from "./select.module.css";

export type SelectOption = { value: string; label: string; disabled?: boolean; lang?: string };

type OptionElement = ReactElement<{
  value?: string | number;
  disabled?: boolean;
  lang?: string;
  children?: ReactNode;
}>;

function text(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return text(node.props.children);
  return "";
}

/** The `<option>` children of a native-style select, as plain options. */
export function optionsOf(children: ReactNode): SelectOption[] {
  const out: SelectOption[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    if (child.type === Fragment) {
      out.push(...optionsOf((child as ReactElement<{ children?: ReactNode }>).props.children));
      return;
    }
    if (child.type !== "option") return;
    const { value, disabled, lang, children: label } = (child as OptionElement).props;
    const shown = text(label);
    out.push({
      value: value === undefined ? shown : String(value),
      label: shown,
      disabled: Boolean(disabled),
      lang,
    });
  });
  return out;
}

type SelectProps = {
  id?: string;
  /** Numbers are compared as text, as a native select does. */
  value: string | number | null | undefined;
  onValueChange: (value: string) => void;
  /** `<option value="…">Label</option>` elements, exactly as inside a native `<select>`. */
  children?: ReactNode;
  /** Or the options as data. */
  options?: readonly SelectOption[];
  disabled?: boolean;
  required?: boolean;
  name?: string;
  /** `sm` for compact pickers inside a row, such as a currency beside an amount. */
  size?: "md" | "sm";
  /** Layout only (width, flex). Look and feel come from the component. */
  className?: string;
  placeholder?: string;
  /** Text direction of the labels: `ltr` for codes such as currencies, `auto` for names. */
  dir?: "ltr" | "rtl" | "auto";
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
};

/**
 * FinPilot's one dropdown, on Base UI's Select: a listbox popup with the select-only combobox
 * pattern. Arrow keys, Home and End, typeahead, Enter or Space to choose, and Escape to close,
 * which returns focus to the button. It mirrors in Arabic and renders above dialogs.
 *
 * A drop-in for `<select>`: label it with `<label htmlFor={id}>` or `aria-label`, give it the
 * same `<option>` children, and swap `onChange` for `onValueChange(value)`.
 */
export function Select({
  id,
  value,
  onValueChange,
  children,
  options: given,
  disabled,
  required,
  name,
  size = "md",
  className,
  placeholder,
  dir,
  ...aria
}: SelectProps) {
  const locale = useLocale() as Locale;
  const portalRoot = useOptionalPortalRoot();
  const options = given ?? optionsOf(children);

  return (
    <DirectionProvider direction={getDirection(locale)}>
      <BaseSelect.Root
        id={id}
        name={name}
        value={value === null || value === undefined ? "" : String(value)}
        onValueChange={(next) => {
          if (typeof next === "string") onValueChange(next);
        }}
        items={options}
        disabled={disabled}
        required={required}
      >
        <BaseSelect.Trigger
          id={id}
          className={cn(s.trigger, size === "sm" && s.sm, className)}
          aria-label={aria["aria-label"]}
          aria-labelledby={aria["aria-labelledby"]}
          aria-describedby={aria["aria-describedby"]}
          aria-invalid={aria["aria-invalid"] || undefined}
        >
          <BaseSelect.Value className={s.value} placeholder={placeholder} dir={dir} />
          <BaseSelect.Icon className={s.icon}>
            <ChevronDown aria-hidden="true" />
          </BaseSelect.Icon>
        </BaseSelect.Trigger>
        <BaseSelect.Portal container={portalRoot ?? undefined}>
          <BaseSelect.Positioner
            className={s.positioner}
            side="bottom"
            align="start"
            sideOffset={6}
            collisionPadding={12}
            alignItemWithTrigger={false}
          >
            <BaseSelect.Popup className={cn(s.popup, size === "sm" && s.popupSm)}>
              <BaseSelect.List className={s.list}>
                {options.map((option) => (
                  <BaseSelect.Item
                    key={option.value}
                    value={option.value}
                    label={option.label}
                    disabled={option.disabled}
                    lang={option.lang}
                    className={s.item}
                  >
                    <BaseSelect.ItemText className={s.itemText} dir={dir}>
                      {option.label}
                    </BaseSelect.ItemText>
                    <BaseSelect.ItemIndicator className={s.check}>
                      <Check aria-hidden="true" />
                    </BaseSelect.ItemIndicator>
                  </BaseSelect.Item>
                ))}
              </BaseSelect.List>
            </BaseSelect.Popup>
          </BaseSelect.Positioner>
        </BaseSelect.Portal>
      </BaseSelect.Root>
    </DirectionProvider>
  );
}
