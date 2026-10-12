import { Link, useLocation } from "@tanstack/react-router";
import { useId, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { cn } from "@/lib/utils";

import { OpenCriticalBadge } from "./nav-badge";
import { NAV_GROUPS, type NavGroup, type NavItem } from "./nav-items";
import { useProjectParams, useProjectQuery } from "./project-context";
import { ProjectSwitcher } from "./project-switcher";
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

/** Everything the rail's page links share: pill shape, lime active state, muted inactive state. */
const RAIL_LINK = {
  className: cn(
    "flex h-11 items-center justify-between rounded-xl px-4 text-sm transition-colors duration-200",
    RAIL_FOCUS,
  ),
  activeProps: {
    className: "bg-lime font-bold text-lime-foreground",
    "aria-current": "page" as const,
  },
  inactiveProps: {
    className:
      "font-medium text-rail-muted-foreground hover:bg-rail-tile hover:text-rail-foreground",
  },
};

/** The link's content: the label, and a dark dot on the active one. */
function railLabel(label: string, badge?: NavItem["badge"]) {
  return ({ isActive }: { isActive: boolean }) => (
    <>
      {label}
      {badge === "open-critical" ? <OpenCriticalBadge active={isActive} /> : null}
      {isActive ? <span aria-hidden className="size-1.5 rounded-full bg-lime-foreground" /> : null}
    </>
  );
}

/**
 * "Settings" while the org's settings are locked behind two-factor authentication: it goes to the
 * account's Security page, which is no child of the settings route, so the active state is read
 * from the address by hand.
 */
function LockedSettingsLink({ label, onNavigate }: { label: string; onNavigate?: () => void }) {
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
      {railLabel(label)({ isActive: onSettings })}
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

  const links = group.items.map(({ label, to, badge }) => (
    <li key={to}>
      {settingsLocked && to === SETTINGS_PATH ? (
        <LockedSettingsLink label={label} onNavigate={onNavigate} />
      ) : (
        <Link to={to} params={params} {...RAIL_LINK} onClick={onNavigate}>
          {railLabel(label, badge)}
        </Link>
      )}
    </li>
  ));

  if (group.heading === null) {
    return <>{links}</>;
  }
  return (
    <li className="mt-3">
      <p id={headingId} className="px-4 pb-1.5 text-overline text-rail-subtle-foreground uppercase">
        {group.heading}
      </p>
      <ul aria-labelledby={headingId} className="flex flex-col gap-1">
        {links}
      </ul>
    </li>
  );
}

/**
 * The floating dark rail (Figma "App/Rail"): logo, project switcher, pill navigation with a
 * lime active state, the "Connect your app" tile and the account tile.
 */
export function Sidebar({ onNavigate, headerAction, className }: SidebarProps) {
  const params = useProjectParams();
  // While the org demands two-factor authentication, its settings are locked: Settings opens the
  // account's Security page instead, the one that is not.
  const settingsLocked = isBlockedByTwoFactor(useProjectQuery());

  return (
    <div
      className={cn(
        "flex h-full flex-col gap-5 overflow-y-auto rounded-card bg-rail px-4 pt-5 pb-4 text-rail-foreground",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <Logo tone="on-dark" />
        {headerAction}
      </div>

      <ProjectSwitcher variant="rail" />

      <nav aria-label="Main">
        <ul className="flex flex-col gap-1">
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

      <div className="mt-auto flex flex-col gap-3">
        <Link
          to="/onboarding"
          search={{ org: params.orgId, project: params.projectId }}
          onClick={onNavigate}
          className={cn(
            "flex flex-col gap-1.5 rounded-[20px] bg-rail-tile p-4 transition-colors duration-200 hover:bg-rail-tile-hover",
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
