import { Link, useNavigate } from "@tanstack/react-router";
import type { MouseEvent } from "react";

import { CostValue } from "@/components/cost-value";
import { RelativeTime } from "@/components/relative-time";
import { ValueOrUnknown } from "@/components/unknown-value";
import { ScoresCell, useScoreTypeOf } from "@/features/scores";
import { useProjectParams } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { RowCheckbox } from "./row-checkbox";
import { TraceStatusDot } from "./status";
import { ModelBadges, TagBadges, TokenPair, TraceErrorLine, UserSession } from "./trace-cells";
import { Cell, TracesTableHead } from "./traces-table-columns";
import { ROW_GRID, SHOW_SCORES_AND_TAGS, SHOW_TOKENS, SHOW_USER } from "./traces-table-grid";
import type { TraceSelection } from "./use-trace-selection";

export { TracesTableSkeleton } from "./traces-table-columns";

interface TracesTableProps {
  traces: TraceSummary[];
  /** The trace opened last; its row is highlighted when you come back to the list. */
  selectedTraceId?: string | null;
  /** Dim rows while results for new filters are loading. */
  stale?: boolean;
  /** Ticked rows for the bulk actions; without it the checkbox column stays empty. */
  selection?: TraceSelection;
}

export function TracesTable({
  traces,
  selectedTraceId = null,
  stale = false,
  selection,
}: TracesTableProps) {
  const navigate = useNavigate();
  const { orgId, projectId } = useProjectParams();
  const scoreTypeOf = useScoreTypeOf();

  function openTrace(event: MouseEvent<HTMLDivElement>, traceId: string) {
    // Let real links/buttons inside the row, and text selection, behave normally.
    const target = event.target as HTMLElement;
    const interactive = target.closest("a, button, input, [role='button'], [tabindex]");
    if (interactive && event.currentTarget.contains(interactive)) {
      return;
    }
    if (window.getSelection()?.toString()) {
      return;
    }
    void navigate({
      to: "/$orgId/$projectId/traces/$traceId",
      params: { orgId, projectId, traceId },
    });
  }

  return (
    <div
      role="table"
      aria-label="Traces"
      aria-busy={stale || undefined}
      className={cn("flex flex-col gap-0.5 transition-opacity", stale && "opacity-60")}
    >
      <TracesTableHead selection={selection} />
      <div role="rowgroup" className="flex flex-col gap-0.5">
        {traces.map((trace) => {
          const ticked = selection?.isSelected(trace.trace_id) ?? false;
          const selected = trace.trace_id === selectedTraceId || ticked;
          return (
            <div
              key={trace.trace_id}
              role="row"
              data-state={selected ? "selected" : undefined}
              onClick={(event) => {
                openTrace(event, trace.trace_id);
              }}
              className={cn(
                ROW_GRID,
                "group/row h-14 cursor-pointer rounded-input text-sm transition-colors",
                selected ? "bg-surface-selected" : "hover:bg-surface-hover",
              )}
            >
              <Cell className="flex justify-center">
                {selection ? (
                  <RowCheckbox
                    checked={ticked}
                    onCheckedChange={() => {
                      selection.toggle(trace);
                    }}
                    label={`Select ${trace.name ?? "unnamed trace"} ${trace.trace_id}`}
                  />
                ) : null}
              </Cell>
              <Cell className="flex justify-center">
                <TraceStatusDot errorCount={trace.error_count} errorMessage={trace.error_message} />
              </Cell>
              <Cell className="min-w-0">
                <TraceName trace={trace} orgId={orgId} projectId={projectId} />
              </Cell>
              <Cell className="truncate text-muted-foreground">
                <RelativeTime iso={trace.started_at} />
              </Cell>
              <Cell className="text-right text-foreground tabular">
                <ValueOrUnknown
                  value={formatDuration(trace.duration_ms)}
                  reason="Duration isn't known yet"
                />
              </Cell>
              <Cell className="min-w-0">
                <ModelBadges models={trace.models} />
              </Cell>
              <Cell className={cn(SHOW_TOKENS, "text-right text-muted-foreground")}>
                <TokenPair input={trace.input_tokens} output={trace.output_tokens} />
              </Cell>
              <Cell className="text-right font-medium text-foreground">
                <CostValue cost={trace.cost_usd} hasUnpriced={trace.has_unpriced} />
              </Cell>
              <Cell className={cn(SHOW_SCORES_AND_TAGS, "min-w-0")}>
                <ScoresCell scores={trace.scores} typeOf={scoreTypeOf} />
              </Cell>
              <Cell className={cn(SHOW_SCORES_AND_TAGS, "min-w-0")}>
                <TagBadges tags={trace.tags} />
              </Cell>
              <Cell className={cn(SHOW_USER, "min-w-0")}>
                <UserSession userId={trace.external_user_id} sessionId={trace.session_id} />
              </Cell>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface TraceNameProps {
  trace: TraceSummary;
  orgId: string;
  projectId: string;
}

/** Bold name (the row's link) over the trace ID in mono, or over the class and message of a failed trace. */
function TraceName({ trace, orgId, projectId }: TraceNameProps) {
  const hasName = trace.name !== null && trace.name !== "";
  return (
    <div className="flex min-w-0 flex-col">
      <Link
        to="/$orgId/$projectId/traces/$traceId"
        params={{ orgId, projectId, traceId: trace.trace_id }}
        className={cn(
          "truncate rounded-sm font-semibold text-foreground hover:underline",
          !hasName && "font-medium text-muted-foreground italic",
        )}
      >
        {hasName ? trace.name : "Unnamed trace"}
      </Link>
      {trace.error_class !== null || trace.error_message ? (
        <TraceErrorLine errorClass={trace.error_class} message={trace.error_message} />
      ) : (
        <span
          title={trace.trace_id}
          className="truncate font-mono text-label text-subtle-foreground group-hover/row:text-muted-foreground"
        >
          {trace.trace_id}
        </span>
      )}
    </div>
  );
}
