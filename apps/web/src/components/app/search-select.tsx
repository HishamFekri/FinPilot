"use client";

import { Combobox } from "@base-ui/react/combobox";
import { DirectionProvider } from "@base-ui/react/direction-provider";
import { Check, ChevronDown, Search } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { getDirection, type Locale } from "@/i18n/routing";
import { cn } from "@/lib/utils";
import { useOptionalPortalRoot } from "./portal-root";
import { type SelectOption, optionsOf } from "./select";
import s from "./select.module.css";

/**
 * A dropdown with a search box at the top of its list, for long lists (currencies, categories,
 * many accounts). Base UI's Combobox with the input inside the popup: arrow keys move through the
 * matches, Enter chooses, Escape closes and returns focus to the button. Same look and same
 * `<option>` children as `Select`.
 */
export function SearchSelect({
  id,
  value,
  onValueChange,
  children,
  options: given,
  size = "md",
  className,
  placeholder,
  dir,
  searchLabel,
  ...aria
}: {
  id?: string;
  value: string | null | undefined;
  onValueChange: (value: string) => void;
  children?: ReactNode;
  options?: readonly SelectOption[];
  size?: "md" | "sm";
  className?: string;
  placeholder?: string;
  dir?: "ltr" | "rtl" | "auto";
  /** What the search box is for, read by screen readers ("Search currencies"). */
  searchLabel?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}) {
  const t = useTranslations("app.select");
  const locale = useLocale() as Locale;
  const portalRoot = useOptionalPortalRoot();
  const options = given ?? optionsOf(children);
  const selected = options.find((o) => o.value === value) ?? null;

  return (
    <DirectionProvider direction={getDirection(locale)}>
      <Combobox.Root
        items={options}
        value={selected}
        onValueChange={(next) => {
          if (next) onValueChange((next as SelectOption).value);
        }}
        itemToStringLabel={(o) => (o as SelectOption).label}
        isItemEqualToValue={(a, b) => (a as SelectOption).value === (b as SelectOption).value}
      >
        <Combobox.Trigger
          id={id}
          className={cn(s.trigger, size === "sm" && s.sm, className)}
          aria-label={aria["aria-label"]}
          aria-labelledby={aria["aria-labelledby"]}
          aria-describedby={aria["aria-describedby"]}
          aria-invalid={aria["aria-invalid"] || undefined}
        >
          <span className={s.value} dir={dir} data-placeholder={selected ? undefined : ""}>
            {selected ? selected.label : (placeholder ?? "")}
          </span>
          <Combobox.Icon className={s.icon}>
            <ChevronDown aria-hidden="true" />
          </Combobox.Icon>
        </Combobox.Trigger>
        <Combobox.Portal container={portalRoot ?? undefined}>
          <Combobox.Positioner
            className={s.positioner}
            side="bottom"
            align="start"
            sideOffset={6}
            collisionPadding={12}
          >
            <Combobox.Popup className={cn(s.popup, s.searchPopup)} aria-label={searchLabel}>
              <div className={s.searchRow}>
                <Search aria-hidden="true" />
                <Combobox.Input
                  className={s.searchInput}
                  placeholder={t("search")}
                  aria-label={searchLabel ?? t("search")}
                />
              </div>
              <Combobox.Empty className={s.emptyResult}>{t("noMatches")}</Combobox.Empty>
              <Combobox.List className={s.list}>
                {(option: SelectOption) => (
                  <Combobox.Item
                    key={option.value}
                    value={option}
                    disabled={option.disabled}
                    lang={option.lang}
                    className={s.item}
                  >
                    <span className={s.itemText} dir={dir}>
                      {option.label}
                    </span>
                    <Combobox.ItemIndicator className={s.check}>
                      <Check aria-hidden="true" />
                    </Combobox.ItemIndicator>
                  </Combobox.Item>
                )}
              </Combobox.List>
            </Combobox.Popup>
          </Combobox.Positioner>
        </Combobox.Portal>
      </Combobox.Root>
    </DirectionProvider>
  );
}
