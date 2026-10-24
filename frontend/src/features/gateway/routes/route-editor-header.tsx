import type { ReactNode } from "react";

import { Breadcrumbs } from "@/components/breadcrumbs";

interface RouteEditorHeaderProps {
  name: string;
  /** Default and version badges, or "Not saved". */
  badges: ReactNode;
  /** The line under the name: who saved it and when, or that the route is new. */
  meta: ReactNode;
  /** The "Routes" crumb: a `Crumb` link, or a `CrumbButton` on an unsaved route. */
  parent: ReactNode;
  /** History and the overflow menu. */
  actions?: ReactNode;
}

/** The route editor's header (Figma "Gateway — Route editor"): breadcrumbs, name, badges, meta. */
export function RouteEditorHeader({ name, badges, meta, parent, actions }: RouteEditorHeaderProps) {
  return (
    <div className="flex flex-col gap-3">
      <Breadcrumbs current={name}>{parent}</Breadcrumbs>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            {/* Focusable by script, so an editor that replaces the list can move focus here. */}
            <h2
              tabIndex={-1}
              className="text-h2 [overflow-wrap:anywhere] text-foreground outline-none"
            >
              {name}
            </h2>
            {badges}
          </div>
          <div className="text-xs font-medium text-muted-foreground">{meta}</div>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
