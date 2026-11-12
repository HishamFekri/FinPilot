"use client";

import { DirectionProvider } from "@base-ui/react/direction-provider";
import { Menu } from "@base-ui/react/menu";
import {
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  CircleUserRound,
  Languages,
  LogOut,
  Monitor,
  Moon,
  MonitorSmartphone,
  Palette,
  Settings2,
  Sun,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { type ReactNode, type RefObject, useState } from "react";
import { getDirection, type Locale, localeNames, routing } from "@/i18n/routing";
import { api } from "@/lib/api";
import { useAuth, useUser } from "./auth-provider";
import s from "./app.module.css";
import { Avatar } from "./ui";

const THEMES = [
  { value: "light", icon: Sun },
  { value: "dark", icon: Moon },
  { value: "system", icon: Monitor },
] as const;

/** Swaps the language prefix of the current URL, keeping the rest. A full load: <html> lang,
 *  dir, and fonts all change with the language. */
export function switchLanguage(code: Locale) {
  const { pathname, search, hash } = window.location;
  const path = pathname.replace(/^\/(en|ar|tr)(?=\/|$)/, `/${code}`);
  window.location.assign(new URL(`${path}${search}${hash}`, window.location.origin).href);
}

/** Language › and Appearance ›, as nested submenus inside any Base UI menu. */
function PreferenceSubmenus({
  container,
  onLanguage,
}: {
  container: RefObject<HTMLElement | null>;
  onLanguage: (code: Locale) => void;
}) {
  const t = useTranslations("prefs");
  const locale = useLocale() as Locale;
  const { theme = "system", setTheme } = useTheme();
  const current = THEMES.find((x) => x.value === theme) ?? THEMES[2];

  return (
    <>
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
              <Menu.RadioGroup
                value={locale}
                onValueChange={(value) => {
                  const code = String(value) as Locale;
                  if (code !== locale) onLanguage(code);
                }}
              >
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
              <Menu.RadioGroup value={theme} onValueChange={(value) => setTheme(String(value))}>
                {THEMES.map(({ value, icon: Icon }) => (
                  <Menu.RadioItem key={value} value={value} closeOnClick className={s.menuItem}>
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
    </>
  );
}

function MenuShell({ children }: { children: ReactNode }) {
  const locale = useLocale() as Locale;
  return <DirectionProvider direction={getDirection(locale)}>{children}</DirectionProvider>;
}

/**
 * The signed-in person's menu: their name and picture, profile, settings and devices, language
 * and appearance, and sign out. Every item works; nothing is listed for a feature that doesn't
 * exist yet.
 *
 * `sidebar` sits at the foot of the navigation rail (it replaces a separate Settings link) and
 * opens beside it; `topbar` is the phone's compact avatar button.
 */
export function UserMenu({
  container,
  placement,
  onNavigate,
  onSignOutError,
}: {
  container: RefObject<HTMLElement | null>;
  placement: "sidebar" | "topbar";
  onNavigate: (path: string) => void;
  onSignOutError: () => void;
}) {
  const t = useTranslations("app.user");
  const user = useUser();
  const { signOut } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const [open, setOpen] = useState(false);
  const name = user.display_name || user.email || t("fallbackName");
  const sidebar = placement === "sidebar";

  // The language is saved to the profile too, so it follows the person to other devices.
  // If saving fails, the page still switches; the URL decides the language (ARCHITECTURE.md §14.3).
  const changeLanguage = async (code: Locale) => {
    try {
      await api("/auth/me", { method: "PATCH", body: { locale: code } });
    } catch {
      /* the switch still happens */
    }
    switchLanguage(code);
  };

  return (
    <MenuShell>
      <Menu.Root modal={false} open={open} onOpenChange={setOpen}>
        <Menu.Trigger
          className={sidebar ? s.profileBtn : s.userBtn}
          aria-label={t("menu", { name })}
          data-placement={placement}
        >
          <Avatar name={user.display_name} email={user.email} src={user.avatar_url} />
          <span className={sidebar ? s.profileName : s.userName} dir="auto">
            {name}
          </span>
          {sidebar ? (
            <ChevronsUpDown className={s.profileChevron} aria-hidden="true" />
          ) : (
            <ChevronDown aria-hidden="true" />
          )}
        </Menu.Trigger>
        <Menu.Portal container={container}>
          <Menu.Positioner
            className={s.menuPositioner}
            side={sidebar ? "inline-end" : "bottom"}
            align="end"
            sideOffset={sidebar ? 12 : 8}
            collisionPadding={12}
          >
            <Menu.Popup className={s.menuPopup}>
              <div className={s.menuHead}>
                <Avatar name={user.display_name} email={user.email} src={user.avatar_url} large />
                <span className={s.menuHeadText}>
                  <span className={s.menuHeadName} dir="auto">
                    {name}
                  </span>
                  {user.email ? (
                    <span className={s.menuHeadEmail} dir="ltr">
                      {user.email}
                    </span>
                  ) : null}
                </span>
              </div>
              <Menu.Item className={s.menuItem} onClick={() => onNavigate("/settings#profile")}>
                <span className={s.menuLabel}>
                  <CircleUserRound aria-hidden="true" />
                  {t("profile")}
                </span>
              </Menu.Item>
              <Menu.Item className={s.menuItem} onClick={() => onNavigate("/settings")}>
                <span className={s.menuLabel}>
                  <Settings2 aria-hidden="true" />
                  {t("settings")}
                </span>
              </Menu.Item>
              <Menu.Item className={s.menuItem} onClick={() => onNavigate("/settings/sessions")}>
                <span className={s.menuLabel}>
                  <MonitorSmartphone aria-hidden="true" />
                  {t("sessions")}
                </span>
              </Menu.Item>
              <Menu.Separator className={s.menuSep} />
              <PreferenceSubmenus container={container} onLanguage={changeLanguage} />
              <Menu.Separator className={s.menuSep} />
              <Menu.Item
                className={`${s.menuItem} ${s.menuDanger}`}
                disabled={signingOut}
                closeOnClick={false}
                onClick={async () => {
                  setSigningOut(true);
                  try {
                    await signOut();
                  } catch {
                    setSigningOut(false);
                    onSignOutError();
                  }
                }}
              >
                <span className={s.menuLabel}>
                  <LogOut aria-hidden="true" />
                  {signingOut ? t("signingOut") : t("signOut")}
                </span>
              </Menu.Item>
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </MenuShell>
  );
}
