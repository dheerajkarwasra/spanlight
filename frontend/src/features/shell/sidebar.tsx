import { Link, useLocation } from "@tanstack/react-router";
import { useId, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { cn } from "@/lib/utils";

import { OpenCriticalBadge } from "./nav-badge";
import { NAV_GROUPS, type NavGroup, type NavItem } from "./nav-items";
import { useProjectParams, useProjectQuery } from "./project-context";
import { ProjectSwitcher } from "./project-switcher";
import { RailScrollArea } from "./rail-scroll-area";
import { isBlockedByTwoFactor } from "./two-factor-gate";
import { UserMenu } from "./user-menu";

interface SidebarProps {
  /** Called after a navigation link is followed (closes the mobile sheet). */
  onNavigate?: () => void;
  /** Rendered to the right of the logo, e.g. the mobile sheet's close and theme buttons. */
  headerAction?: ReactNode;
  className?: string;
}

/** Lime focus ring: the violet ring does not reach 3:1 on the near-black rail. */
const RAIL_FOCUS =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime";

const SETTINGS_PATH: NavItem["to"] = "/$orgId/$projectId/settings";

/** Everything the rail's page links share: a quiet row, a soft active state with a lime dot. */
const RAIL_LINK = {
  className: cn(
    "flex h-9 items-center gap-2.5 rounded-lg px-3 text-sm font-medium transition-colors duration-150",
    RAIL_FOCUS,
  ),
  activeProps: {
    className: "bg-rail-tile-hover text-rail-foreground",
    "aria-current": "page" as const,
  },
  inactiveProps: {
    className: "text-rail-muted-foreground hover:bg-rail-tile hover:text-rail-foreground",
  },
};

/** The link's content: icon, label, an optional count, and a lime dot on the active one. */
function railLabel({ label, icon: Icon, badge }: Pick<NavItem, "label" | "icon" | "badge">) {
  return ({ isActive }: { isActive: boolean }) => (
    <>
      <Icon aria-hidden className="size-4 shrink-0 opacity-80" strokeWidth={1.75} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {badge === "open-critical" ? <OpenCriticalBadge /> : null}
      {isActive ? <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-lime" /> : null}
    </>
  );
}

/**
 * "Settings" while the org's settings are locked behind two-factor authentication: it goes to the
 * account's Security page, which is no child of the settings route, so the active state is read
 * from the address by hand.
 */
function LockedSettingsLink({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  const onSettings = useLocation({
    select: (location) => /\/settings(\/|$)/.test(location.pathname),
  });
  const { className, activeProps, inactiveProps } = RAIL_LINK;

  return (
    <Link
      to="/account/security"
      onClick={onNavigate}
      aria-current={onSettings ? "page" : undefined}
      className={cn(className, onSettings ? activeProps.className : inactiveProps.className)}
    >
      {railLabel(item)({ isActive: onSettings })}
    </Link>
  );
}

interface NavGroupItemsProps {
  group: NavGroup;
  settingsLocked: boolean;
  onNavigate: (() => void) | undefined;
}

/** One group of rail links. A labelled group nests its own list under an overline heading. */
function NavGroupItems({ group, settingsLocked, onNavigate }: NavGroupItemsProps) {
  const params = useProjectParams();
  const headingId = useId();

  const links = group.items.map((item) => (
    <li key={item.to}>
      {settingsLocked && item.to === SETTINGS_PATH ? (
        <LockedSettingsLink item={item} onNavigate={onNavigate} />
      ) : (
        <Link to={item.to} params={params} {...RAIL_LINK} onClick={onNavigate}>
          {railLabel(item)}
        </Link>
      )}
    </li>
  ));

  if (group.heading === null) {
    return <>{links}</>;
  }
  return (
    <li className="mt-4">
      <p id={headingId} className="px-3 pb-1 text-overline text-rail-subtle-foreground uppercase">
        {group.heading}
      </p>
      <ul aria-labelledby={headingId} className="flex flex-col gap-0.5">
        {links}
      </ul>
    </li>
  );
}

/**
 * The floating dark rail (Figma "App/Rail"): logo, project switcher, grouped navigation with a
 * quiet active row and a lime dot, the "Connect your app" tile and the account tile. The header and footer stay
 * put and only the navigation scrolls, with the current page kept in view (Figma "Fixes
 * 2026-10-13"). On a window too short for that (600 px or less) the whole rail scrolls instead.
 */
export function Sidebar({ onNavigate, headerAction, className }: SidebarProps) {
  const params = useProjectParams();
  const pathname = useLocation({ select: (location) => location.pathname });
  // While the org demands two-factor authentication, its settings are locked: Settings opens the
  // account's Security page instead, the one that is not.
  const settingsLocked = isBlockedByTwoFactor(useProjectQuery());

  return (
    <div
      className={cn(
        "flex h-full flex-col gap-5 overflow-y-auto rounded-card bg-rail px-3 pt-5 pb-3 text-rail-foreground dark:border dark:border-border",
        className,
      )}
    >
      <div className="flex shrink-0 items-center justify-between gap-2">
        <div className="px-1">
          <Logo tone="on-dark" />
        </div>
        {headerAction}
      </div>

      <ProjectSwitcher variant="rail" />

      <RailScrollArea
        revealKey={pathname}
        className="flex-1 [@media(max-height:37.5rem)]:flex-none"
      >
        <nav aria-label="Main">
          <ul className="flex flex-col gap-0.5">
            {NAV_GROUPS.filter((group) => group.items.length > 0).map((group, index) => (
              <NavGroupItems
                key={group.heading ?? index}
                group={group}
                settingsLocked={settingsLocked}
                onNavigate={onNavigate}
              />
            ))}
          </ul>
        </nav>
      </RailScrollArea>

      <div className="flex shrink-0 flex-col gap-3">
        <Link
          to="/onboarding"
          search={{ org: params.orgId, project: params.projectId }}
          onClick={onNavigate}
          className={cn(
            "flex flex-col gap-1 rounded-tile bg-rail-tile p-3.5 transition-colors duration-150 hover:bg-rail-tile-hover",
            RAIL_FOCUS,
          )}
        >
          <span className="text-sm font-semibold text-rail-foreground">Connect your app</span>
          <span className="text-xs font-medium text-rail-subtle-foreground">
            Python SDK, OpenAI, Anthropic or OTLP.
          </span>
        </Link>
        <UserMenu />
      </div>
    </div>
  );
}
