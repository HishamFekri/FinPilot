"use client";

import { Dialog } from "@base-ui/react/dialog";
import { Tooltip } from "@base-ui/react/tooltip";
import { CloudOff, Ellipsis, MonitorSmartphone, Settings2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { type ReactNode, type RefObject, useContext, useRef, useState } from "react";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/utils";
import { AuthProvider, useAuth, useUser } from "./auth-provider";
import { FinanceProvider } from "./finance/finance-provider";
import { AddButton, QuickAddDialog } from "./finance/quick-add-dialog";
import s from "./app.module.css";
import { UserMenu } from "./menus";
import { DeletionPendingBanner } from "./privacy-view";
import { PortalRootContext } from "./portal-root";
import { activeKey, NAV, type NavItem, pageWidth, TAB_KEYS } from "./nav";
import { Avatar, Button, Notice, Spinner } from "./ui";

/** The shell's root element: dialogs and menus render inside it, so they inherit its theme. */
const PortalRoot = PortalRootContext;

export function usePortalRoot(): RefObject<HTMLDivElement | null> {
  const ref = useContext(PortalRoot);
  if (!ref) throw new Error("usePortalRoot must be used inside the app shell");
  return ref;
}

/** Everything under /{locale}/app: the session check, then the shell around each page. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <Gate>{children}</Gate>
    </AuthProvider>
  );
}

/** Nothing private renders until the API has confirmed the session. */
function Gate({ children }: { children: ReactNode }) {
  const t = useTranslations("app.gate");
  const shell = useTranslations("shell");
  const { status, refresh } = useAuth();
  const [retrying, setRetrying] = useState(false);

  if (status === "signed-in") return <Shell>{children}</Shell>;

  return (
    <div className={cn(s.theme, s.gate)}>
      <div className={s.gateCard}>
        <span className={s.brand}>{shell("brand")}</span>
        {status === "unavailable" ? (
          <>
            <span className={s.gateIcon}>
              <CloudOff aria-hidden="true" />
            </span>
            <h1 className={s.gateTitle}>{t("unavailableTitle")}</h1>
            <p className={s.muted} role="alert">
              {t("unavailableBody")}
            </p>
            <Button
              variant="primary"
              busy={retrying}
              onClick={async () => {
                setRetrying(true);
                await refresh();
                setRetrying(false);
              }}
            >
              {t("retry")}
            </Button>
          </>
        ) : (
          <p className={s.muted} role="status">
            <Spinner /> <span>{status === "leaving" ? t("leaving") : t("checking")}</span>
          </p>
        )}
      </div>
    </div>
  );
}

function useNavLabel() {
  const t = useTranslations("app.nav");
  return (item: NavItem) => t(item.key);
}

function SidebarLink({ item, current }: { item: NavItem; current: boolean }) {
  const t = useTranslations("app.nav");
  const label = useNavLabel()(item);
  const Icon = item.icon;
  const link = (
    <Link
      href={`/app${item.path}`}
      className={s.navLink}
      aria-current={current ? "page" : undefined}
    >
      <Icon aria-hidden="true" />
      <span className={s.navLabel}>{label}</span>
      {item.built ? null : <span className={s.navSoon}>{t("soon")}</span>}
    </Link>
  );
  // On the tablet rail only the icons show; a tooltip names each one. The link's own text
  // (visually hidden there) remains its accessible name.
  return (
    <Tooltip.Root>
      <Tooltip.Trigger render={link} />
      <Tooltip.Portal>
        <Tooltip.Positioner side="inline-end" sideOffset={10}>
          <Tooltip.Popup className={s.tooltip}>
            {item.built ? label : `${label} · ${t("soon")}`}
          </Tooltip.Popup>
        </Tooltip.Positioner>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const t = useTranslations("app");
  const shell = useTranslations("shell");
  const label = useNavLabel();
  const user = useUser();
  const pathname = usePathname();
  const router = useRouter();
  const rootRef = useRef<HTMLDivElement>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [signOutFailed, setSignOutFailed] = useState(false);
  const current = activeKey(pathname);
  const moreItems = NAV.filter((i) => !TAB_KEYS.includes(i.key));
  const moreActive = current === "settings" || moreItems.some((i) => i.key === current);
  const onSessions = pathname.endsWith("/sessions");
  const name = user.display_name || user.email || t("user.fallbackName");
  const userMenu = (placement: "sidebar" | "topbar") => (
    <UserMenu
      container={rootRef}
      placement={placement}
      onNavigate={(path) => router.push(`/app${path}`)}
      onSignOutError={() => setSignOutFailed(true)}
    />
  );

  return (
    <PortalRoot.Provider value={rootRef}>
      <FinanceProvider>
        <div ref={rootRef} className={cn(s.theme, s.shell)}>
          <a
            className={s.skip}
            href="#main"
            onClick={(event) => {
              // Move keyboard focus too, not just the scroll position.
              event.preventDefault();
              document.getElementById("main")?.focus();
            }}
          >
            {t("skip")}
          </a>

          <aside className={s.sidebar}>
            <Link href="/app" className={s.brand}>
              {shell("brand")}
            </Link>
            {/* New transaction. Home has its own, in its header. */}
            {current === "home" ? null : (
              <div className={s.sidebarAdd}>
                <AddButton variant="sidebar" className={s.sidebarAddButton} />
              </div>
            )}
            <Tooltip.Provider delay={300}>
              <nav aria-label={t("nav.label")} className={s.sideNav}>
                <ul className={s.nav}>
                  {NAV.map((item) => (
                    <li key={item.key}>
                      <SidebarLink item={item} current={item.key === current} />
                    </li>
                  ))}
                </ul>
              </nav>
            </Tooltip.Provider>
            {/* Settings, devices, language, appearance and sign out all live in this menu. */}
            <div className={s.sidebarProfile}>{userMenu("sidebar")}</div>
          </aside>

          <div className={s.main}>
            <header className={s.topbar}>
              <Link href="/app" className={s.brand}>
                {shell("brand")}
              </Link>
              {/* Phones only: each page has its own heading, so there's no title here. */}
              <div className={s.topbarEnd}>
                <AddButton />
                <div className={s.topbarProfile}>{userMenu("topbar")}</div>
              </div>
            </header>

            <main id="main" tabIndex={-1} className={s.content} data-width={pageWidth(pathname)}>
              {signOutFailed ? (
                <Notice tone="error" className={s.pageNotice}>
                  {t("user.signOutFailed")}
                </Notice>
              ) : null}
              <DeletionPendingBanner />
              <div key={pathname} className={s.pageIn}>
                {children}
              </div>
            </main>
          </div>

          <nav className={s.tabbar} aria-label={t("nav.label")}>
            {NAV.filter((item) => TAB_KEYS.includes(item.key)).map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.key}
                  href={`/app${item.path}`}
                  className={s.tab}
                  aria-current={item.key === current ? "page" : undefined}
                >
                  <Icon aria-hidden="true" />
                  <span className={s.tabLabel}>{label(item)}</span>
                </Link>
              );
            })}
            <Dialog.Root open={moreOpen} onOpenChange={setMoreOpen}>
              <Dialog.Trigger className={s.tab} aria-current={moreActive ? "page" : undefined}>
                <Ellipsis aria-hidden="true" />
                <span className={s.tabLabel}>{t("nav.more")}</span>
              </Dialog.Trigger>
              <Dialog.Portal container={rootRef}>
                <Dialog.Backdrop className={s.sheetBackdrop} />
                <Dialog.Popup className={s.sheet}>
                  <div className={s.sheetGrip} aria-hidden="true" />
                  <Dialog.Title className={s.sheetTitle}>{t("nav.moreTitle")}</Dialog.Title>
                  <Link
                    href="/app/settings#profile"
                    className={s.sheetProfile}
                    onClick={() => setMoreOpen(false)}
                  >
                    <Avatar name={user.display_name} email={user.email} src={user.avatar_url} />
                    <span className={s.sheetProfileText}>
                      <span className={s.menuHeadName} dir="auto">
                        {name}
                      </span>
                      <span className={s.sheetProfileHint}>{t("user.profile")}</span>
                    </span>
                  </Link>
                  <ul className={s.nav}>
                    {moreItems.map((item) => {
                      const Icon = item.icon;
                      return (
                        <li key={item.key}>
                          <Link
                            href={`/app${item.path}`}
                            className={s.navLink}
                            aria-current={item.key === current ? "page" : undefined}
                            onClick={() => setMoreOpen(false)}
                          >
                            <Icon aria-hidden="true" />
                            <span className={s.navLabel}>{label(item)}</span>
                            {item.built ? null : <span className={s.navSoon}>{t("nav.soon")}</span>}
                          </Link>
                        </li>
                      );
                    })}
                    <li>
                      <Link
                        href="/app/settings"
                        className={s.navLink}
                        aria-current={current === "settings" && !onSessions ? "page" : undefined}
                        onClick={() => setMoreOpen(false)}
                      >
                        <Settings2 aria-hidden="true" />
                        <span className={s.navLabel}>{t("user.settings")}</span>
                      </Link>
                    </li>
                    <li>
                      <Link
                        href="/app/settings/sessions"
                        className={s.navLink}
                        aria-current={onSessions ? "page" : undefined}
                        onClick={() => setMoreOpen(false)}
                      >
                        <MonitorSmartphone aria-hidden="true" />
                        <span className={s.navLabel}>{t("user.sessions")}</span>
                      </Link>
                    </li>
                  </ul>
                </Dialog.Popup>
              </Dialog.Portal>
            </Dialog.Root>
          </nav>
          <QuickAddDialog />
        </div>
      </FinanceProvider>
    </PortalRoot.Provider>
  );
}
