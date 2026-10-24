import { Link } from "@tanstack/react-router";
import { GitCompareArrows } from "lucide-react";
import { useId, useState } from "react";

import { UnknownValue } from "@/components/unknown-value";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectParams } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";
import { formatDuration, formatRelativeTime, shortId } from "@/lib/format";

import { PasteForm, type ThisTrace } from "./compare-paste-form";
import { compareSearch } from "./search";
import { usePreviousRunsQuery } from "./trace-queries";

const LABEL = "Compare with…";

/**
 * Figma "Trace detail / Compare with" (C1): the previous run of this trace's name, up to 5 more
 * recent runs before it, or a pasted trace id. Each goes to the compare page, earlier trace as A.
 * The label hides below the `@6xl` container width, like Replay's.
 */
export function CompareWithPopover({ trace }: { trace: ThisTrace }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <TooltipRoot>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button size="sm" variant="secondary" aria-label={LABEL}>
              <GitCompareArrows aria-hidden />
              <span className="hidden @6xl:inline">{LABEL}</span>
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Compare with another run</TooltipContent>
      </TooltipRoot>
      <PopoverContent
        align="end"
        aria-label="Compare with another run"
        className="flex max-h-(--radix-popover-content-available-height) w-80 max-w-[calc(100vw-2rem)] flex-col gap-4 overflow-y-auto"
      >
        {trace.name === null ? (
          <p className="text-sm text-muted-foreground">
            This trace has no name, so there are no other runs of it to list. Paste a trace ID.
          </p>
        ) : (
          <Runs trace={trace} name={trace.name} />
        )}
        <PasteForm trace={trace} />
      </PopoverContent>
    </Popover>
  );
}

function Runs({ trace, name }: { trace: ThisTrace; name: string }) {
  const runs = usePreviousRunsQuery(name, trace.started_at);
  if (runs.isPending) {
    return (
      <div role="status" aria-label="Loading earlier runs" className="flex flex-col gap-2">
        <Skeleton className="h-10" />
        <Skeleton className="h-10" />
      </div>
    );
  }
  if (runs.isError) {
    return (
      <div
        role="alert"
        className="flex items-center justify-between gap-2 text-sm text-muted-foreground"
      >
        Couldn&apos;t load earlier runs.
        <Button size="sm" variant="ghost" onClick={() => void runs.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  const [previous, ...recent] = runs.data.items.filter((run) => run.trace_id !== trace.trace_id);
  return (
    <>
      <RunList
        title="Previous run of"
        name={name}
        runs={previous ? [previous] : []}
        thisId={trace.trace_id}
      >
        No earlier run in the 30 days before this one.
      </RunList>
      {recent.length > 0 ? (
        <RunList
          title="Recent runs of"
          name={name}
          runs={recent.slice(0, 5)}
          thisId={trace.trace_id}
        />
      ) : null}
    </>
  );
}

interface RunListProps {
  title: string;
  name: string;
  runs: readonly TraceSummary[];
  thisId: string;
  /** Shown when `runs` is empty. */
  children?: string;
}

function RunList({ title, name, runs, thisId, children }: RunListProps) {
  const headingId = useId();
  const { orgId, projectId } = useProjectParams();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1.5">
      <h3 id={headingId} className="truncate text-xs font-medium text-muted-foreground">
        {title} <code className="font-mono text-foreground">{name}</code>
      </h3>
      {runs.length === 0 ? (
        <p className="text-sm text-muted-foreground">{children}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {runs.map((run) => (
            <li key={run.trace_id}>
              {/* Every listed run started before this trace, so it is the baseline. */}
              <Link
                from="/$orgId/$projectId/traces/$traceId"
                to="/$orgId/$projectId/traces/compare"
                params={{ orgId, projectId }}
                search={(prev) => compareSearch(prev, run.trace_id, thisId)}
                className="flex min-h-10 items-center justify-between gap-3 rounded-tile px-2 text-sm hover:bg-surface-hover"
              >
                <span className="text-foreground">{formatRelativeTime(run.started_at)}</span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="tabular">
                    {formatDuration(run.duration_ms) ?? (
                      <UnknownValue reason="Duration isn't known yet: the run hasn't ended." />
                    )}
                  </span>
                  <span className="font-mono">{shortId(run.trace_id)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
