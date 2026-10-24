import { Link, useLocation } from "@tanstack/react-router";
import { PanelLeftClose } from "lucide-react";
import { Fragment, type ComponentProps, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { cn } from "@/lib/utils";

import { OpenCriticalBadge } from "./nav-badge";
import { HUBS, hubForPath, type Hub } from "./nav-items";
import { useProjectParams, useProjectQuery } from "./project-context";
import { ProjectSwitcher } from "./project-switcher";
import { RailScrollArea } from "./rail-scroll-area";
import { isBlockedByTwoFactor } from "./two-factor-gate";
import { useHasTraces } from "./use-has-traces";
import { UserMenu } from "./user-menu";

interface SidebarProps {
  /** Called after a navigation link is followed (closes the mobile sheet). */
  onNavigate?: () => void;
  /** Rendered to the right of the logo, e.g. the mobile sheet's close and theme buttons. */
  headerAction?: ReactNode;
  /** Shows the "Collapse sidebar" toggle (the desktop rail at 1280 px and up). */
  onCollapse?: () => void;
  onOpenShortcuts?: () => void;
  className?: string;
}

/** Lime focus ring: the violet ring does not reach 3:1 on the near-black rail. */
export const RAIL_FOCUS =
  "focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-lime";

/** Everything the rail's hub links share: a quiet row, a soft active state with a lime dot. */
function railRowClass(active: boolean) {
  return cn(
    "flex h-9 items-center gap-2.5 rounded-lg px-3 text-sm font-medium transition-colors duration-150",
    RAIL_FOCUS,
    active
      ? "bg-rail-tile-hover text-rail-foreground"
      : "text-rail-muted-foreground hover:bg-rail-tile hover:text-rail-foreground",
  );
}

/** The link's content: icon, label, an optional count, and a lime dot on the active one. */
function RailLabel({ hub, active }: { hub: Hub; active: boolean }) {
  const Icon = hub.icon;
  return (
    <>
      <Icon aria-hidden className="size-4 shrink-0 opacity-80" strokeWidth={1.75} />
      <span className="min-w-0 flex-1 truncate">{hub.label}</span>
      {hub.badge === "open-critical" ? <OpenCriticalBadge /> : null}
      {active ? <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-lime" /> : null}
    </>
  );
}

interface HubLinkProps extends Omit<ComponentProps<"a">, "href" | "aria-current"> {
  hub: Hub;
  active: boolean;
  settingsLocked: boolean;
}

/**
 * One hub link, to the hub's first tab; the rail and the icon rail style it. While the org's
 * settings are locked behind two-factor authentication, Settings goes to the account's Security
 * page instead. Other props (a tooltip's handlers, `aria-label`) reach the anchor.
 */
export function HubLink({ hub, active, settingsLocked, ...rest }: HubLinkProps) {
  const params = useProjectParams();
  const shared = {
    ...rest,
    // The router marks a fuzzy match current on its own (Settings under a reconciliation page),
    // so only an exact match is left to it; the hub decides the rest.
    activeOptions: { exact: true },
    "aria-current": active ? ("page" as const) : undefined,
  };

  if (settingsLocked && hub.id === "settings") {
    return <Link to="/account/security" {...shared} />;
  }
  return <Link to={hub.tabs[0].to} params={params} {...shared} />;
}

/**
 * The floating dark rail (Figma "App/Rail"): logo, project switcher, the hubs in two runs split by a
 * hairline with a quiet active row and a lime dot, the "Connect your app" tile (until the first trace) and the account tile. The header and footer stay
 * put and only the navigation scrolls, with the current page kept in view (Figma "Fixes
 * 2026-10-13"). On a window too short for that (600 px or less) the whole rail scrolls instead.
 */
export function Sidebar({
  onNavigate,
  headerAction,
  onCollapse,
  onOpenShortcuts,
  className,
}: SidebarProps) {
  const params = useProjectParams();
  const pathname = useLocation({ select: (location) => location.pathname });
  // While the org demands two-factor authentication, its settings are locked: Settings opens the
  // account's Security page instead, the one that is not.
  const settingsLocked = isBlockedByTwoFactor(useProjectQuery());
  const activeHub = hubForPath(pathname)?.id;
  const hasTraces = useHasTraces();

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
            {HUBS.map((hub, index) => (
              <Fragment key={hub.id}>
                {hub.split === "bottom" && HUBS[index - 1]?.split === "top" ? (
                  <li role="none" aria-hidden className="my-2.5 h-px bg-rail-tile" />
                ) : null}
                <li>
                  <HubLink
                    hub={hub}
                    active={activeHub === hub.id}
                    settingsLocked={settingsLocked}
                    onClick={onNavigate}
                    className={railRowClass(activeHub === hub.id)}
                  >
                    <RailLabel hub={hub} active={activeHub === hub.id} />
                  </HubLink>
                </li>
              </Fragment>
            ))}
          </ul>
        </nav>
      </RailScrollArea>

      {onCollapse ? (
        <button
          type="button"
          onClick={onCollapse}
          className={cn(railRowClass(false), "-mt-3 shrink-0")}
        >
          <PanelLeftClose aria-hidden className="size-4 shrink-0 opacity-80" strokeWidth={1.75} />
          Collapse sidebar
        </button>
      ) : null}

      <div className="flex shrink-0 flex-col gap-3">
        {/* Only before the first trace; the user menu always offers it too. */}
        {hasTraces === false ? (
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
        ) : null}
        <UserMenu onOpenShortcuts={onOpenShortcuts} />
      </div>
    </div>
  );
}
