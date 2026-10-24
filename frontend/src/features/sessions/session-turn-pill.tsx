import { useNavigate } from "@tanstack/react-router";
import { ChevronDown } from "lucide-react";
import { useId, useMemo, useState, type SyntheticEvent } from "react";

import { ErrorState } from "@/components/error-state";
import { useProjectParams } from "@/features/shell/project-context";
import { useTraceQuery } from "@/features/traces";
import { buildSpanTree, SpanWaterfall, SpanWaterfallSkeleton } from "@/features/traces/waterfall";
import type { SessionTurn, Span } from "@/lib/api";
import { cn } from "@/lib/utils";

import { turnPillText } from "./session-format";

/** The waterfall's row ids are `span-row-<span id>`. */
const ROW_ID_PREFIX = "span-row-";

/**
 * Spec §8 S3: the pill between a turn's input and its answer. It expands into a read-only mini
 * waterfall of the trace (three levels open), loaded on first expand; a row opens the trace page
 * at that span.
 */
export function TurnPill({ turn }: { turn: SessionTurn }) {
  const [open, setOpen] = useState(false);
  const { text, reason } = turnPillText(turn);
  const panelId = useId();
  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        title={reason ?? undefined}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          setOpen((current) => !current);
        }}
        className="inline-flex min-h-6 items-center gap-1.5 self-start rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted-foreground tabular transition-colors hover:bg-surface-hover hover:text-foreground"
      >
        {text}
        {reason !== null ? <span className="sr-only"> ({reason})</span> : null}
        <ChevronDown
          aria-hidden
          className={cn(
            "size-3 transition-transform motion-reduce:transition-none",
            open && "rotate-180",
          )}
        />
      </button>
      {open ? (
        <div id={panelId}>
          <TurnWaterfall traceId={turn.trace.trace_id} />
        </div>
      ) : null}
    </div>
  );
}

function TurnWaterfall({ traceId }: { traceId: string }) {
  const traceQuery = useTraceQuery(traceId);
  if (traceQuery.isPending) {
    return <SpanWaterfallSkeleton />;
  }
  if (traceQuery.isError) {
    return (
      <ErrorState
        compact
        error={traceQuery.error}
        title="Couldn't load this turn's steps"
        onRetry={() => {
          void traceQuery.refetch();
        }}
      />
    );
  }
  return <ReadOnlyWaterfall traceId={traceId} spans={traceQuery.data.spans} />;
}

function ReadOnlyWaterfall({ traceId, spans }: { traceId: string; spans: Span[] }) {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  // Arrow keys move the selection; only a click or Enter on a row leaves for the trace page.
  const [selected, setSelected] = useState<string | null>(null);

  function openRow(event: SyntheticEvent) {
    const row = (event.target as Element).closest('[role="treeitem"]');
    if (!row?.id.startsWith(ROW_ID_PREFIX)) return;
    void navigate({
      to: "/$orgId/$projectId/traces/$traceId",
      params: { orgId, projectId, traceId },
      search: { span: row.id.slice(ROW_ID_PREFIX.length) },
    });
  }

  return (
    <div
      onClick={openRow}
      onKeyDown={(event) => {
        if (event.key === "Enter") openRow(event);
      }}
    >
      <SpanWaterfall
        spans={spans}
        roots={roots}
        selectedSpanId={selected}
        onSelect={setSelected}
        maxDepth={3}
      />
    </div>
  );
}
