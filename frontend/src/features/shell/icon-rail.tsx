import { useLocation } from "@tanstack/react-router";
import { PanelLeftOpen } from "lucide-react";
import { Fragment } from "react";

import { Logo } from "@/components/logo";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { useOpenCriticalCount } from "./use-open-critical-count";
import { HUBS, hubForPath, type Hub } from "./nav-items";
import { useProjectQuery } from "./project-context";
import { HubLink, RAIL_FOCUS } from "./sidebar";
import { isBlockedByTwoFactor } from "./two-factor-gate";
import { UserMenu } from "./user-menu";

interface IconRailProps {
  /** "Expand sidebar": the full rail, in place or in the navigation sheet. */
  onExpand: () => void;
  /** Below 1280 px "Expand sidebar" opens the navigation sheet, a dialog. */
  expandOpensSheet: boolean;
  onOpenShortcuts?: () => void;
}

/** A 40 px square on the rail: the expand button and each hub link. */
function tileClass(active: boolean) {
  return cn(
    "relative flex size-10 items-center justify-center rounded-lg transition-colors duration-150",
    RAIL_FOCUS,
    active
      ? "bg-rail-tile-hover text-rail-foreground"
      : "text-rail-muted-foreground hover:bg-rail-tile hover:text-rail-foreground",
  );
}

/** The link's name: the hub label, and on Monitor the Doctor's open critical count. */
function linkName(hub: Hub, openCritical: number | undefined) {
  if (hub.badge !== "open-critical" || !openCritical) {
    return hub.label;
  }
  return `${hub.label}, ${openCritical} open critical ${openCritical === 1 ? "finding" : "findings"}`;
}

/**
 * The 56 px rail (Figma "Rail / Icon 56"): logo mark, "Expand sidebar", the hubs as icons with
 * tooltips in the same two runs, and the user avatar opening the same menu as the full rail.
 */
export function IconRail({ onExpand, expandOpensSheet, onOpenShortcuts }: IconRailProps) {
  const pathname = useLocation({ select: (location) => location.pathname });
  const settingsLocked = isBlockedByTwoFactor(useProjectQuery());
  const activeHub = hubForPath(pathname)?.id;
  const openCritical = useOpenCriticalCount();

  return (
    <div className="flex h-full flex-col items-center gap-4 overflow-y-auto rounded-card bg-rail py-4 text-rail-foreground dark:border dark:border-border">
      <Logo tone="on-dark" withWordmark={false} className="shrink-0" />

      <Tooltip content="Expand sidebar" side="right">
        <button
          type="button"
          aria-label="Expand sidebar"
          aria-haspopup={expandOpensSheet ? "dialog" : undefined}
          onClick={onExpand}
          className={cn(tileClass(false), "shrink-0 bg-rail-tile hover:bg-rail-tile-hover")}
        >
          <PanelLeftOpen aria-hidden className="size-4" strokeWidth={1.75} />
        </button>
      </Tooltip>

      <nav aria-label="Main" className="flex-1">
        <ul className="flex flex-col items-center gap-1">
          {HUBS.map((hub, index) => {
            const Icon = hub.icon;
            const active = activeHub === hub.id;
            return (
              <Fragment key={hub.id}>
                {hub.split === "bottom" && HUBS[index - 1]?.split === "top" ? (
                  <li role="none" aria-hidden className="my-2 h-px w-6 bg-rail-tile" />
                ) : null}
                <li>
                  <Tooltip content={hub.label} side="right">
                    <HubLink
                      hub={hub}
                      active={active}
                      settingsLocked={settingsLocked}
                      aria-label={linkName(hub, openCritical)}
                      className={tileClass(active)}
                    >
                      <Icon aria-hidden className="size-4" strokeWidth={1.75} />
                      {hub.badge === "open-critical" && openCritical ? (
                        <span
                          aria-hidden
                          className="absolute top-1.5 right-1.5 size-2 rounded-full border border-lime bg-danger"
                        />
                      ) : null}
                    </HubLink>
                  </Tooltip>
                </li>
              </Fragment>
            );
          })}
        </ul>
      </nav>

      <UserMenu variant="avatar" onOpenShortcuts={onOpenShortcuts} />
    </div>
  );
}
