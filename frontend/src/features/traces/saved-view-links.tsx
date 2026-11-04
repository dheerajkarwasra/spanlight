import { Link } from "@tanstack/react-router";

import { cn } from "@/lib/utils";

import { activeViewId, TRACE_VIEWS, viewSearch } from "./saved-views";
import type { TraceFilters } from "./search";

/**
 * Built-in views above the trace filters (Figma "Traces — Saved views (G-032)"): each one is a
 * link that sets the list's filters and sort, keeping the environment and time range.
 */
export function SavedViews({ search }: { search: TraceFilters }) {
  const active = activeViewId(search);
  return (
    <nav
      aria-label="Saved views"
      className="-mx-4 [scrollbar-width:none] overflow-x-auto px-4 md:mx-0 md:px-0"
    >
      <ul className="inline-flex items-center gap-0.5 rounded-full border border-border bg-surface-muted p-1">
        {TRACE_VIEWS.map((view) => {
          const current = view.id === active;
          return (
            <li key={view.id}>
              <Link
                from="/$orgId/$projectId/traces"
                to="."
                search={(prev) => ({ ...prev, ...viewSearch(view) })}
                // Router matching is a subset match, so "All" would match every URL; exact keeps
                // its aria-current in line with `activeViewId`.
                activeOptions={{ exact: true }}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "inline-flex items-center rounded-full px-3.5 py-1.5 text-sm font-medium whitespace-nowrap text-muted-foreground",
                  "transition-[background-color,color,box-shadow] duration-200 ease-out-quart",
                  current
                    ? // Dark: the fill is close to the track, so a ring marks it (as in SegmentedControl).
                      "bg-surface font-semibold text-foreground shadow-card dark:bg-surface-hover dark:shadow-none dark:ring-1 dark:ring-border-strong dark:ring-inset"
                    : "hover:text-foreground",
                )}
              >
                {view.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
