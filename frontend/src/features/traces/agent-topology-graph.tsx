import { useMemo } from "react";

import { Card } from "@/components/ui/card";
import { formatCompact, formatCost, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { kindMeta } from "./span-kind-meta";
import type { SpanNode } from "./span-tree";
import { buildTopologyLayout } from "./trace-agent-utils";

interface AgentTopologyGraphProps {
  roots: readonly SpanNode[];
  selectedSpanId: string | null;
  onSelect: (spanId: string) => void;
}

/**
 * Interactive DAG/tree view of an agent trace grouped by call depth so multi-step loops,
 * sub-agents, LLM turns, and tool fan-outs read at a glance without scrolling a dense table.
 */
export function AgentTopologyGraph({ roots, selectedSpanId, onSelect }: AgentTopologyGraphProps) {
  const layout = useMemo(() => buildTopologyLayout(roots), [roots]);

  if (layout.nodes.length === 0) {
    return null;
  }

  return (
    <Card className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{layout.nodes.length} spans laid out left-to-right by call depth</span>
        <span>Click any node to open it in the inspector</span>
      </div>
      <div className="overflow-auto rounded-md border border-border bg-background/60 p-2">
        <div
          className="relative"
          style={{ width: `${layout.width}px`, height: `${layout.height}px` }}
        >
          <svg
            aria-hidden="true"
            width={layout.width}
            height={layout.height}
            className="pointer-events-none absolute inset-0"
          >
            {layout.edges.map((edge) => {
              const midX = (edge.x1 + edge.x2) / 2;
              const d = `M ${edge.x1} ${edge.y1} C ${midX} ${edge.y1}, ${midX} ${edge.y2}, ${edge.x2} ${edge.y2}`;
              return (
                <path
                  key={`${edge.fromId}->${edge.toId}`}
                  d={d}
                  fill="none"
                  className="stroke-border-strong"
                  strokeWidth={1.5}
                />
              );
            })}
          </svg>
          {layout.nodes.map(({ span, x, y }) => {
            const meta = kindMeta(span.kind);
            const Icon = meta.icon;
            const selected = span.span_id === selectedSpanId;
            const errored = span.status === "error";
            const totalTokens = (span.input_tokens ?? 0) + (span.output_tokens ?? 0);
            const costLabel = formatCost(span.cost_usd);
            const durLabel = formatDuration(span.duration_ms);

            return (
              <button
                key={span.span_id}
                type="button"
                aria-pressed={selected}
                aria-label={`${meta.label}: ${span.name}${errored ? " (error)" : ""}`}
                onClick={() => {
                  onSelect(span.span_id);
                }}
                style={{
                  left: `${x}px`,
                  top: `${y}px`,
                  width: `${layout.nodeWidth}px`,
                  height: `${layout.nodeHeight}px`,
                }}
                className={cn(
                  "absolute flex flex-col justify-between rounded-lg border px-2.5 py-2 text-left transition-colors",
                  "bg-surface hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-ring",
                  selected && "border-ring ring-2 ring-ring",
                  errored && !selected && "border-destructive",
                  !selected && !errored && "border-border",
                )}
              >
                <div className="flex min-w-0 items-center justify-between gap-1.5">
                  <span className="flex min-w-0 items-center gap-1.5 text-xs font-medium text-foreground">
                    <Icon className={cn("h-3.5 w-3.5 shrink-0", meta.textClass)} aria-hidden />
                    <span className="truncate">{span.name}</span>
                  </span>
                  {errored ? (
                    <span className="bg-destructive/15 text-destructive shrink-0 rounded px-1 text-[10px] font-medium">
                      err
                    </span>
                  ) : null}
                </div>
                <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground tabular">
                  <span className="truncate">{span.model ?? meta.label}</span>
                  <span className="shrink-0">
                    {durLabel ?? "—"}
                    {totalTokens > 0 ? ` · ${formatCompact(totalTokens) ?? totalTokens}t` : ""}
                    {costLabel ? ` · ${costLabel}` : ""}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
