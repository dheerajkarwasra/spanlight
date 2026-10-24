import { Link } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";

import { StatusDot } from "@/components/status-dot";
import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell/project-context";

import type { HeroStatus } from "./hero";

interface OverviewHeroProps {
  /**
   * The date line: `full` on wider screens ("Thursday, 8 October · production · Last 24 hours"),
   * `compact` on phones, where the filters sit right above the page ("… · Last 24h").
   * `trailing` follows it on the same line, e.g. the "Estimated · hourly rollups" chip.
   */
  eyebrow: { full: string; compact: string; trailing?: ReactNode };
  /** "Good afternoon, Dheeraj." */
  greeting: string;
  /** The status sentence, or null while it is unknown (loading) or unavailable (error). */
  status: HeroStatus | null;
  isLoading: boolean;
  /** Shows the "Listening for traces" pill instead of the actions (first run). */
  listeningEnvironment?: string | null;
}

/**
 * The page title (spec §4 O2): the date and filters, then one row with the greeting and the
 * window's status sentence as a single 28 px line (24 px on phones), the serif accent on its last
 * words, and the action on the right.
 */
export function OverviewHero({
  eyebrow,
  greeting,
  status,
  isLoading,
  listeningEnvironment,
}: OverviewHeroProps) {
  const firstRun = status?.kind === "first-run";

  return (
    <div className="flex flex-col gap-2">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-overline text-muted-foreground uppercase">
        {firstRun ? null : (
          <StatusDot
            state="live"
            pulse={false}
            className="ring-[1.5px] ring-foreground ring-inset max-sm:hidden"
          />
        )}
        <span className="sm:hidden">{eyebrow.compact}</span>
        <span className="max-sm:hidden">{eyebrow.full}</span>
        {eyebrow.trailing}
      </p>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
        <h1 className="min-w-0 text-[1.5rem] leading-tight font-extrabold tracking-[-0.03em] [overflow-wrap:anywhere] text-foreground sm:text-[1.75rem]">
          <span className="font-semibold text-muted-foreground">{greeting}</span>
          {status ? (
            <>
              {" "}
              {status.lead}{" "}
              <span className="font-serif-accent text-[1.75rem] leading-none font-normal tracking-normal text-accent sm:text-[2rem]">
                {status.accent}
              </span>
            </>
          ) : null}
          {isLoading ? (
            // A span, not <Skeleton> (a div): headings may only contain phrasing content.
            <span
              aria-hidden
              className="ml-2 inline-block h-6 w-56 max-w-full animate-shimmer rounded-md bg-surface-muted bg-linear-to-r from-surface-muted via-surface-hover to-surface-muted bg-size-[200%_100%] align-middle sm:h-7 sm:w-72"
            />
          ) : null}
        </h1>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {firstRun ? (
            <ListeningPill environment={listeningEnvironment ?? null} />
          ) : (
            <ExploreTracesButton />
          )}
        </div>
      </div>
    </div>
  );
}

function ExploreTracesButton() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Button asChild variant="primary" size="lg" className="w-full sm:w-auto">
      {/* The project route keeps the range and environment in the URL, so the traces page opens
          on the same window. */}
      <Link to="/$orgId/$projectId/traces" params={{ orgId, projectId }}>
        Explore traces
        <ArrowUpRight aria-hidden className="size-3.5!" />
      </Link>
    </Button>
  );
}

function ListeningPill({ environment }: { environment: string | null }) {
  return (
    <p
      role="status"
      className="inline-flex items-center gap-2.5 rounded-full border border-border bg-surface py-2.5 pr-4 pl-3.5 text-sm font-medium text-foreground shadow-card"
    >
      <StatusDot state="live" className="ring-[1.5px] ring-foreground ring-inset" />
      {environment ? `Listening for traces on ${environment}` : "Listening for new traces"}
    </p>
  );
}
