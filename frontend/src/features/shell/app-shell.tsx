import { Outlet, useLocation, useMatch } from "@tanstack/react-router";
import { X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { NotFoundPage } from "@/app/route-pages";
import { ErrorState } from "@/components/error-state";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { VerifyEmailBanner } from "@/features/auth";
import { isApiError, isTwoFactorRequired } from "@/lib/api";
import { writeLastProject } from "@/lib/last-project";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import { CommandPalette } from "./command-palette";
import { FocusShell } from "./focus-shell";
import { HubTabs } from "./hub-tabs";
import { IconRail } from "./icon-rail";
import { hubForPath, showsHubTabs } from "./nav-items";
import { useProjectParams, useProjectQuery } from "./project-context";
import { ShortcutHelp } from "./shortcut-help";
import { Sidebar } from "./sidebar";
import { MobileAppBar, MobileDataFilters, Topbar } from "./topbar";
import { TwoFactorRequired } from "./two-factor-required";
import { useOnAccountSettingsPath, useOnSecurityPath, useTwoFactorGate } from "./two-factor-gate";
import { hasEnabledFilter, useDataFilters } from "./use-data-filters";
import { useKeyboardShortcuts } from "./use-keyboard-shortcuts";
import { useRailCollapsed } from "./use-rail-collapsed";

/**
 * The signed-in layout.
 *
 * Desktop (≥ 1024px): canvas with a 12px gutter, the floating dark rail on the left and the
 * rounded main panel with an in-panel top bar on the right. The page scrolls the
 * window; the rail stays pinned. From 1280px the rail is expanded unless the viewer collapsed it;
 * below that it is the 56px icon rail, whose "Expand sidebar" opens the full rail in the sheet.
 *
 * Phones and tablets: no gutter, a sticky app bar, the rail in a sheet, and the data filters
 * in a row above the page content.
 *
 * Pages render inside `<main>`, which already provides horizontal padding (16px / 40px), top
 * spacing and a 1440px max width. Pages should not add their own outer padding.
 */
export function AppShell() {
  // Review work mode is a focus screen: the page draws its own frame, without rail or top bar.
  // Read from the matches the outlet renders, not the location: while a navigation is pending
  // the location is still the old one, so the page would mount in one shell and then remount in
  // the other (finding T9-03: work mode claimed an item twice).
  const workMode = useMatch({
    from: "/_authed/$orgId/$projectId/annotate/$queueId",
    shouldThrow: false,
  });
  if (workMode !== undefined) {
    return <FocusShell />;
  }
  return <ChromeShell />;
}

function ChromeShell() {
  const params = useProjectParams();
  const projectQuery = useProjectQuery();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // Tailwind's `xl`: the only width where the full rail fits beside the page.
  const wide = useMediaQuery("(min-width: 80rem)");
  const [railCollapsed, setRailCollapsed] = useRailCollapsed();
  const iconRail = !wide || railCollapsed;
  const twoFactorBlocked = useTwoFactorGate(projectQuery);
  // Account settings call only `/auth/*`: a failed project query must not replace them, which
  // would also unmount a setup wizard that is holding recovery codes.
  const onAccountSettings = useOnAccountSettingsPath();
  // The Security page's 2FA card carries the same "verify your email" prompt, so it has no banner.
  const onSecurityPage = useOnSecurityPath();
  // Data controls are for pages that can show data, and there are none while the org is locked.
  const routeDataFilters = useDataFilters();
  const dataFilters = twoFactorBlocked ? null : routeDataFilters;

  // One modal at a time: the palette replaces the keyboard help instead of stacking on it.
  const togglePalette = useCallback(() => {
    setHelpOpen(false);
    setPaletteOpen((current) => !current);
  }, []);
  const openPalette = useCallback(() => {
    setMobileNavOpen(false);
    setHelpOpen(false);
    setPaletteOpen(true);
  }, []);
  const openHelp = useCallback(() => {
    setMobileNavOpen(false);
    setHelpOpen(true);
  }, []);
  const closeMobileNav = useCallback(() => {
    setMobileNavOpen(false);
  }, []);

  useKeyboardShortcuts(togglePalette, openHelp);
  const pathname = useLocation({ select: (location) => location.pathname });
  const hub = hubForPath(pathname);

  useEffect(() => {
    if (projectQuery.isSuccess) {
      writeLastProject({ orgId: params.orgId, projectId: params.projectId });
    }
  }, [params.orgId, params.projectId, projectQuery.isSuccess]);

  if (projectQuery.isError && isApiError(projectQuery.error) && projectQuery.error.isNotFound) {
    return <NotFoundPage />;
  }

  return (
    <div className="min-h-dvh bg-background lg:flex lg:gap-3 lg:bg-canvas lg:p-3">
      <a
        href="#main"
        className="sr-only z-50 rounded-full bg-surface px-4 py-2 text-sm font-semibold shadow-lg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>

      <aside className={cn("hidden shrink-0 lg:block", iconRail ? "w-14" : "w-60")}>
        <div className="sticky top-3 h-[calc(100dvh-1.5rem)]">
          {iconRail ? (
            <IconRail
              expandOpensSheet={!wide}
              onOpenShortcuts={openHelp}
              onExpand={() => {
                if (wide) {
                  setRailCollapsed(false);
                } else {
                  setMobileNavOpen(true);
                }
              }}
            />
          ) : (
            <Sidebar
              onOpenShortcuts={openHelp}
              onCollapse={() => {
                setRailCollapsed(true);
              }}
            />
          )}
        </div>
      </aside>

      <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <SheetContent side="left" hideClose className="w-[264px] border-0 bg-transparent p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">
            Switch projects and move between pages
          </SheetDescription>
          <Sidebar
            onNavigate={closeMobileNav}
            onOpenShortcuts={openHelp}
            headerAction={
              <div className="flex items-center gap-1.5">
                <ThemeToggle tone="rail" />
                <SheetClose asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Close navigation"
                    className="bg-rail-tile text-rail-foreground hover:bg-rail-tile-hover focus-visible:outline-lime"
                  >
                    <X aria-hidden />
                  </Button>
                </SheetClose>
              </div>
            }
          />
        </SheetContent>
      </Sheet>

      <div className="relative flex min-w-0 flex-1 flex-col overflow-clip bg-background lg:min-h-[calc(100dvh-1.5rem)] lg:rounded-card">
        <MobileAppBar
          className="lg:hidden"
          onOpenCommandPalette={openPalette}
          onOpenNavigation={() => {
            setMobileNavOpen(true);
          }}
        />

        <div className="relative mx-auto hidden w-full max-w-[1440px] px-10 pt-7 lg:block">
          <Topbar onOpenCommandPalette={openPalette} dataFilters={dataFilters} />
        </div>

        {dataFilters && hasEnabledFilter(dataFilters) ? (
          <div className="relative px-4 pt-3 lg:hidden">
            <MobileDataFilters options={dataFilters} />
          </div>
        ) : null}

        <main
          id="main"
          tabIndex={-1}
          className="relative mx-auto flex w-full max-w-[1440px] min-w-0 flex-1 flex-col px-4 pt-6 pb-12 outline-none lg:px-10 lg:pt-8"
        >
          {onSecurityPage ? null : <VerifyEmailBanner />}
          {twoFactorBlocked ? (
            <TwoFactorRequired />
          ) : projectQuery.isError &&
            !isTwoFactorRequired(projectQuery.error) &&
            !onAccountSettings ? (
            <ErrorState
              error={projectQuery.error}
              onRetry={() => {
                void projectQuery.refetch();
              }}
            />
          ) : (
            <>
              {hub && showsHubTabs(pathname) ? <HubTabs hub={hub} /> : null}
              <Outlet />
            </>
          )}
        </main>
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} onOpenShortcuts={openHelp} />
      <ShortcutHelp open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  );
}
