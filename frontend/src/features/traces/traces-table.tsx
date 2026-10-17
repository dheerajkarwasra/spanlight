import { Link, useNavigate } from "@tanstack/react-router";
import type { MouseEvent } from "react";

import { CostValue, UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import { RelativeTime } from "@/components/relative-time";
import { ValueOrUnknown } from "@/components/unknown-value";
import { ScoresCell, useScoreTypeOf } from "@/features/scores";
import { useProjectParams, useProjectQuery } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { RetryCostCell, RetryCountCell } from "./retry-cell";
import type { RetryCostColumn } from "./retry-format";
import { MetadataChips } from "./metadata-chips";
import { RowCheckbox } from "./row-checkbox";
import { TraceStatusDot } from "./status";
import { ModelBadges, TagBadges, TokenPair, TraceErrorLine, UserSession } from "./trace-cells";
import { Cell, TracesTableHead } from "./traces-table-columns";
import { tableLayout } from "./traces-table-grid";
import type { TraceSelection } from "./use-trace-selection";

export { TracesTableSkeleton } from "./traces-table-columns";

const NO_KEYS: readonly string[] = [];

interface TracesTableProps {
  traces: TraceSummary[];
  /** The trace opened last; its row is highlighted when you come back to the list. */
  selectedTraceId?: string | null;
  /** Dim rows while results for new filters are loading. */
  stale?: boolean;
  /** Ticked rows for the bulk actions; without it the checkbox column stays empty. */
  selection?: TraceSelection;
  /** The Retry cost column, shown while the list is sorted or filtered by retries. */
  retryCost?: RetryCostColumn;
}

export function TracesTable({
  traces,
  selectedTraceId = null,
  stale = false,
  selection,
  retryCost = "hidden",
}: TracesTableProps) {
  const navigate = useNavigate();
  const promotedKeys = useProjectQuery().data?.promoted_metadata_keys ?? NO_KEYS;
  const layout = tableLayout(retryCost !== "hidden", promotedKeys.length > 0);
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
      <TracesTableHead
        selection={selection}
        layout={layout}
        sortedByRetryCost={retryCost === "sorted"}
      />
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
                layout.row,
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
              {layout.metadata !== null ? (
                <Cell className={cn(layout.metadata, "min-w-0")}>
                  <MetadataChips metadata={trace.metadata} promoted={promotedKeys} max={1} linked />
                </Cell>
              ) : null}
              <Cell className="truncate text-muted-foreground">
                <RelativeTime iso={trace.started_at} />
              </Cell>
              <Cell className="text-right text-foreground tabular">
                <ValueOrUnknown
                  value={formatDuration(trace.duration_ms)}
                  reason="Duration isn't known yet"
                />
              </Cell>
              <Cell className={cn(layout.models, "min-w-0")}>
                <ModelBadges models={trace.models} />
              </Cell>
              <Cell className={cn(layout.tokens, "text-right text-muted-foreground")}>
                <TokenPair input={trace.input_tokens} output={trace.output_tokens} />
              </Cell>
              <Cell className="text-right font-medium text-foreground">
                <CostValue
                  cost={trace.cost_usd}
                  hasUnpriced={trace.has_unpriced}
                  unknownReason={UNKNOWN_TOTAL_REASON}
                />
              </Cell>
              <Cell className={cn(layout.retries, "text-right")}>
                <RetryCountCell trace={trace} />
              </Cell>
              {layout.retryCost ? (
                <Cell className="text-right">
                  <RetryCostCell trace={trace} />
                </Cell>
              ) : null}
              <Cell className={cn(layout.scoresAndTags, "min-w-0 overflow-hidden")}>
                <ScoresCell scores={trace.scores} typeOf={scoreTypeOf} />
              </Cell>
              <Cell className={cn(layout.scoresAndTags, "min-w-0 overflow-hidden")}>
                <TagBadges tags={trace.tags} max={1} />
              </Cell>
              <Cell className={cn(layout.user, "min-w-0")}>
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
