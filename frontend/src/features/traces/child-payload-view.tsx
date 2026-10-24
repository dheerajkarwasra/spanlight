import { CornerDownRight } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Span } from "@/lib/api";

import { PayloadSection, PayloadTitle, type PayloadLabel } from "./payload-section";
import { useSpanQuery } from "./trace-queries";

export interface ChildFallback {
  /** LLM descendants that may hold the payload, best first (`llmInputCandidates`/`…Output…`). */
  candidates: readonly Span[];
  traceId: string;
  onSelectSpan: (spanId: string) => void;
}

/**
 * Figma "D4": a span's I/O borrowed from an LLM descendant. A candidate whose payload the trace
 * detail left out is fetched whole; if it turns out empty, the next candidate is tried.
 */
export function ChildPayloadView({
  label,
  child,
  fallback,
}: {
  label: PayloadLabel;
  child: Span;
  /** `candidates` holds the ones after `child`. */
  fallback: ChildFallback;
}) {
  const full = useSpanQuery(fallback.traceId, child);

  const source = (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-input bg-surface-muted px-3 py-2">
      <CornerDownRight aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="text-xs text-muted-foreground">From child span</span>
        <span title={child.name} className="truncate font-mono text-xs text-foreground">
          {child.name}
        </span>
      </div>
      <Button
        size="sm"
        aria-label={`Select span ${child.name}`}
        onClick={() => {
          fallback.onSelectSpan(child.span_id);
        }}
        className="h-7 px-3"
      >
        Select span
      </Button>
    </div>
  );

  if (child.payload_omitted && (full.isPending || full.isError)) {
    return (
      <section aria-label={label} className="flex flex-col gap-2.5">
        <PayloadTitle label={label} />
        {source}
        {full.isPending ? (
          <div role="status" aria-label={`Loading ${label.toLowerCase()}`}>
            <Skeleton className="h-24 w-full rounded-tile" />
          </div>
        ) : (
          <ErrorState
            compact
            error={full.error}
            title={`Couldn't load the child span's ${label.toLowerCase()}`}
            onRetry={() => {
              void full.refetch();
            }}
          />
        )}
      </section>
    );
  }

  const span = full.data ?? child;
  const value = label === "Input" ? span.input : span.output;
  if (value === null) {
    const [next, ...after] = fallback.candidates;
    return next ? (
      <ChildPayloadView label={label} child={next} fallback={{ ...fallback, candidates: after }} />
    ) : (
      <PayloadSection label={label} value={null} spanTruncated={false} childrenChecked />
    );
  }
  return (
    <PayloadSection
      label={label}
      value={value}
      spanTruncated={span.truncated}
      childrenChecked
      source={source}
    />
  );
}
