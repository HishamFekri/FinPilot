"use client";

import { DirectionProvider } from "@base-ui/react/direction-provider";
import { Menu } from "@base-ui/react/menu";
import {
  Check,
  ChevronRight,
  EllipsisVertical,
  Languages,
  Monitor,
  Moon,
  Palette,
  Sun,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { type RefObject, useEffect, useState } from "react";
import { getDirection, type Locale, localeNames, routing } from "@/i18n/routing";
import s from "./landing.module.css";

const THEMES = [
  { value: "light", icon: Sun },
  { value: "dark", icon: Moon },
  { value: "system", icon: Monitor },
] as const;

/**
 * The ⋮ settings menu: Language › and Appearance ›, each a real nested submenu. Base UI's Menu owns
 * the hard parts: roles (menu, menuitem, menuitemradio), arrow keys, opening a submenu with the
 * inline-end arrow, Escape closing one level at a time, outside clicks, and returning focus.
 * Popups render inside `container` so they inherit the landing page's theme variables.
 */
export function SettingsMenu({ container }: { container: RefObject<HTMLElement | null> }) {
  const t = useTranslations("landing.settings");
  const locale = useLocale() as Locale;
  const { theme = "system", setTheme } = useTheme();
  const current = THEMES.find((x) => x.value === theme) ?? THEMES[2];
  const [open, setOpen] = useState(false);

  // The controls leave when the page scrolls; an open menu goes with them.
  useEffect(() => {
    if (!open) return;
    const onScroll = () => {
      if (window.scrollY > 24) setOpen(false);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [open]);

  // A full page load: <html> lang, dir, and fonts all change with the language. The #section is
  // kept, and next-intl remembers the choice in its cookie.
  const goTo = (code: string) => {
    if (code === locale) return;
    const next = new URL(
      `/${code}${window.location.search}${window.location.hash}`,
      window.location.origin,
    );
    window.location.assign(next.href);
  };

  return (
    <DirectionProvider direction={getDirection(locale)}>
      <Menu.Root modal={false} open={open} onOpenChange={setOpen}>
        <Menu.Trigger className={s.dotsBtn} aria-label={t("button")}>
          <EllipsisVertical aria-hidden="true" />
        </Menu.Trigger>
        <Menu.Portal container={container}>
          <Menu.Positioner className={s.menuPositioner} side="bottom" align="end" sideOffset={8}>
            <Menu.Popup className={s.menuPopup}>
              <Menu.SubmenuRoot>
                <Menu.SubmenuTrigger className={s.menuItem}>
                  <span className={s.menuLabel}>
                    <Languages aria-hidden="true" />
                    {t("language")}
                  </span>
                  <span className={s.menuValue}>
                    {localeNames[locale]}
                    <ChevronRight className={s.menuChevron} aria-hidden="true" />
                  </span>
                </Menu.SubmenuTrigger>
                <Menu.Portal container={container}>
                  <Menu.Positioner className={s.menuPositioner} sideOffset={6} alignOffset={-6}>
                    <Menu.Popup className={s.menuPopup}>
                      <Menu.RadioGroup value={locale} onValueChange={(v) => goTo(String(v))}>
                        {routing.locales.map((code) => (
                          <Menu.RadioItem
                            key={code}
                            value={code}
                            closeOnClick
                            className={s.menuItem}
                            lang={code}
                          >
                            <span>{localeNames[code]}</span>
                            <Menu.RadioItemIndicator className={s.menuCheck}>
                              <Check aria-hidden="true" />
                            </Menu.RadioItemIndicator>
                          </Menu.RadioItem>
                        ))}
                      </Menu.RadioGroup>
                    </Menu.Popup>
                  </Menu.Positioner>
                </Menu.Portal>
              </Menu.SubmenuRoot>

              <Menu.SubmenuRoot>
                <Menu.SubmenuTrigger className={s.menuItem}>
                  <span className={s.menuLabel}>
                    <Palette aria-hidden="true" />
                    {t("appearance")}
                  </span>
                  <span className={s.menuValue}>
                    {t(current.value)}
                    <ChevronRight className={s.menuChevron} aria-hidden="true" />
                  </span>
                </Menu.SubmenuTrigger>
                <Menu.Portal container={container}>
                  <Menu.Positioner className={s.menuPositioner} sideOffset={6} alignOffset={-6}>
                    <Menu.Popup className={s.menuPopup}>
                      <Menu.RadioGroup value={theme} onValueChange={(v) => setTheme(String(v))}>
                        {THEMES.map(({ value, icon: Icon }) => (
                          <Menu.RadioItem
                            key={value}
                            value={value}
                            closeOnClick
                            className={s.menuItem}
                          >
                            <span className={s.menuLabel}>
                              <Icon aria-hidden="true" />
                              {t(value)}
                            </span>
                            <Menu.RadioItemIndicator className={s.menuCheck}>
                              <Check aria-hidden="true" />
                            </Menu.RadioItemIndicator>
                          </Menu.RadioItem>
                        ))}
                      </Menu.RadioGroup>
                    </Menu.Popup>
                  </Menu.Positioner>
                </Menu.Portal>
              </Menu.SubmenuRoot>
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </DirectionProvider>
  );
}
