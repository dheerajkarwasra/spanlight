import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight, ChevronUp, Info } from "lucide-react";
import { useId, useState } from "react";

import { StatusDot } from "@/components/status-dot";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { UNKNOWN_DASH_CLASS } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import type { EvidenceSpan } from "@/lib/api";
import { shortId } from "@/lib/format";

/** Traces shown before "Show all". */
export const COLLAPSED_TRACES = 5;

const STATUS_WORDS: Record<string, string> = { error: "failed", ok: "succeeded" };
const ROW = "flex min-h-11 items-center gap-3 rounded-tile px-3 py-2 text-sm md:min-h-10";

interface EvidenceTraceLinksProps {
  /** At most 20, newest first. */
  traceIds: readonly string[];
  /** The span each stored trace opens on; a trace without one is no longer stored. */
  spans: readonly EvidenceSpan[];
}

/**
 * Figma "Example traces": one row per evidence trace that opens it on its offending span (status,
 * short id, span name, model), "Trace no longer stored" for one retention removed, and "Show all
 * 20 traces" past the first five.
 */
export function EvidenceTraceLinks({ traceIds, spans }: EvidenceTraceLinksProps) {
  const titleId = useId();
  const listId = useId();
  const [expanded, setExpanded] = useState(false);
  const total = traceIds.length;
  const shown = expanded ? traceIds : traceIds.slice(0, COLLAPSED_TRACES);
  const byTrace = new Map(spans.map((span) => [span.trace_id, span]));

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-col gap-1">
        <h2 id={titleId} className="text-card">
          Example traces
        </h2>
        <p className="text-xs font-medium text-muted-foreground">
          {total === 0
            ? "No example traces were recorded for this finding."
            : `Each opens on its first failed span, or its first LLM span. ${String(shown.length)} of ${String(total)} shown, newest first.`}
        </p>
      </div>
      {total === 0 ? null : (
        <ul id={listId} aria-label="Example traces" className="flex flex-col gap-2">
          {shown.map((traceId) => {
            const span = byTrace.get(traceId);
            return (
              <li key={traceId}>
                {span === undefined ? <GoneRow traceId={traceId} /> : <SpanRow span={span} />}
              </li>
            );
          })}
        </ul>
      )}
      {total > COLLAPSED_TRACES ? (
        <Button
          size="sm"
          aria-expanded={expanded}
          aria-controls={listId}
          className="w-fit max-md:h-11"
          onClick={() => {
            setExpanded((value) => !value);
          }}
        >
          {expanded ? "Show fewer traces" : `Show all ${String(total)} traces`}
          {expanded ? <ChevronUp aria-hidden /> : <ChevronDown aria-hidden />}
        </Button>
      ) : null}
    </Card>
  );
}

/** The whole row opens the trace with its span selected. */
function SpanRow({ span }: { span: EvidenceSpan }) {
  const { orgId, projectId } = useProjectParams();
  const status = STATUS_WORDS[span.status] ?? "status not set";
  const label = `Open trace ${span.trace_id} on span ${span.name}, ${status}, ${span.model ?? "model not reported"}`;
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId: span.trace_id }}
      search={(prev) => ({ ...prev, span: span.span_id })}
      aria-label={label}
      className={`${ROW} bg-surface-muted transition-colors hover:bg-surface-hover`}
    >
      <StatusDot state={span.status === "error" || span.status === "ok" ? span.status : "unset"} />
      <span className="w-20 shrink-0 font-mono text-xs">{shortId(span.trace_id)}</span>
      <span title={span.name} className="min-w-0 flex-1 truncate">
        {span.name}
      </span>
      <span className="hidden max-w-48 truncate font-mono text-xs text-muted-foreground sm:block">
        {span.model ?? <span className={UNKNOWN_DASH_CLASS}>—</span>}
      </span>
      <span className="flex shrink-0 items-center gap-0.5 rounded-full border border-border bg-surface px-2.5 py-1 text-xs font-semibold">
        Open
        <ChevronRight aria-hidden className="size-3.5" />
      </span>
    </Link>
  );
}

/** A trace retention removed: its id as text, nothing to open. */
function GoneRow({ traceId }: { traceId: string }) {
  return (
    <p className={`${ROW} border border-border text-muted-foreground`}>
      <Info aria-hidden className="size-4 shrink-0" />
      <span className="w-20 shrink-0 font-mono text-xs">{shortId(traceId)}</span>
      <span>Trace no longer stored</span>
    </p>
  );
}
