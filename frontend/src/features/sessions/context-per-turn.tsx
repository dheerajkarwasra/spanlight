import { Link } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";

import { Sparkline } from "@/components/sparkline";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectInsights } from "@/features/doctor";
import { useProjectParams } from "@/features/shell/project-context";
import type { TraceSummary } from "@/lib/api";
import { formatCost, formatInteger } from "@/lib/format";

import { cumulativeCost } from "./session-summary";

/** Open context-growth findings the link is looked for in; the API cites ≤ 20 traces each. */
const INSIGHT_LIMIT = 50;

/**
 * Spec §8 S5: input tokens per turn as small bars (one series, a tooltip per bar, no legend), the
 * cumulative cost as its own 36 px sparkline (never a second axis), and a link to an open Doctor
 * context-growth finding that cites one of the session's traces.
 */
export function ContextPerTurn({ traces }: { traces: readonly TraceSummary[] }) {
  const max = Math.max(0, ...traces.map((trace) => trace.input_tokens));
  const costs = cumulativeCost(traces);
  const peak = traces.findIndex((trace) => trace.input_tokens === max);
  const summary =
    max === 0
      ? "No input tokens recorded"
      : `Input tokens per turn: ${formatInteger(traces[0]?.input_tokens ?? 0) ?? ""} on the first, ${formatInteger(traces.at(-1)?.input_tokens ?? 0) ?? ""} on the last, peak ${formatInteger(max) ?? ""} on turn ${peak + 1}`;

  const reached = costs.findLastIndex((cost) => cost !== null);
  const costLine =
    reached === -1
      ? "No turn has a known cost yet"
      : `Reached ${formatCost(costs[reached]) ?? ""} by turn ${reached + 1}`;

  return (
    <div className="flex flex-col gap-3">
      <div role="list" aria-label={summary} className="flex h-12 items-end gap-0.5">
        {traces.map((trace, index) => {
          const label = `Turn ${index + 1} · ${formatInteger(trace.input_tokens) ?? ""} input tokens`;
          // A zero-token turn draws no bar; any other keeps a sliver so it stays visible.
          const height =
            max === 0 || trace.input_tokens === 0
              ? 0
              : `${Math.max((trace.input_tokens / max) * 100, 2)}%`;
          return (
            <TooltipRoot key={trace.trace_id}>
              <TooltipTrigger asChild>
                {/* The full-height hit area, focusable so each value is reachable by keyboard. */}
                <span
                  role="listitem"
                  tabIndex={0}
                  aria-label={label}
                  className="flex h-full min-w-0 flex-1 items-end rounded-xs"
                >
                  <span className="w-full rounded-t-[4px] bg-chart-1" style={{ height }} />
                </span>
              </TooltipTrigger>
              <TooltipContent>{label}</TooltipContent>
            </TooltipRoot>
          );
        })}
      </div>
      <div className="flex flex-col gap-1">
        {/* The total sits in "Total cost" above; the line shows how it built up. */}
        <p className="text-xs font-medium text-muted-foreground">Cumulative cost</p>
        <Sparkline values={costs} tone="cost" zeroBased className="h-9" />
        <p className="sr-only">{costLine}</p>
      </div>
      <ContextGrowthLink traceIds={traces.map((trace) => trace.trace_id)} />
    </div>
  );
}

function ContextGrowthLink({ traceIds }: { traceIds: string[] }) {
  const { orgId, projectId } = useProjectParams();
  const insights = useProjectInsights(
    { status: ["open"], kind: "context_growth", limit: INSIGHT_LIMIT },
    { retry: false },
  ).data?.items;
  const ids = new Set(traceIds);
  const finding = insights?.find((insight) => insight.evidence.trace_ids.some((id) => ids.has(id)));
  if (!finding) return null;
  return (
    <Link
      to="/$orgId/$projectId/doctor/$insightId"
      params={{ orgId, projectId, insightId: finding.id }}
      className="inline-flex items-center gap-0.5 self-start rounded-xs text-xs font-medium text-accent hover:underline"
    >
      Doctor: context growth
      <ArrowUpRight aria-hidden className="size-3" />
    </Link>
  );
}
