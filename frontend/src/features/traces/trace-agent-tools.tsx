import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { Card } from "@/components/ui/card";
import { pricesApi, type Span } from "@/lib/api";
import { formatCompact, formatCost, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import { computePrefixComparisons, simulateTraceModels } from "./trace-agent-utils";

interface TraceAgentToolsProps {
  spans: readonly Span[];
  onSelectSpan: (spanId: string) => void;
}

/**
 * Agent diagnostics panel pairing prompt prefix divergence analysis (why KV/prompt cache missed
 * across consecutive LLM turns) with a trace-wide model-swap cost simulator.
 */
export function TraceAgentTools({ spans, onSelectSpan }: TraceAgentToolsProps) {
  const comparisons = useMemo(() => computePrefixComparisons(spans), [spans]);
  const pricesQuery = useQuery({
    queryKey: ["prices", "list"],
    queryFn: () => pricesApi.list(),
    staleTime: 5 * 60_000,
  });
  const simulation = useMemo(
    () => simulateTraceModels(spans, pricesQuery.data ?? []),
    [spans, pricesQuery.data],
  );

  return (
    <div className="flex flex-col gap-3">
      <Card className="flex flex-col gap-3 p-4">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Prompt prefix stability</h3>
          <p className="text-xs text-muted-foreground">
            Compares consecutive LLM requests in this trace to highlight where prefixes diverged.
          </p>
        </div>
        {comparisons.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Need at least two LLM spans with captured inputs to compare prefixes.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {comparisons.map((entry, idx) => {
              const pct = formatPercent(entry.prefixRatio) ?? "0%";
              const weakCache = entry.prefixRatio < 0.5 && entry.prevChars >= 200;
              return (
                <div
                  key={`${entry.prevSpanId}-${entry.currSpanId}`}
                  className={cn(
                    "flex flex-col gap-1.5 rounded-md border p-2.5 text-xs",
                    weakCache ? "border-warning/60 bg-warning/5" : "border-border bg-background/50",
                  )}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        onSelectSpan(entry.currSpanId);
                      }}
                      className="text-left font-medium text-foreground hover:underline"
                    >
                      Turn {idx + 2}: {entry.currName}
                    </button>
                    <span className="text-muted-foreground tabular">
                      Prefix match: <strong className="text-foreground">{pct}</strong>
                      {entry.cachedTokens !== null
                        ? ` · ${formatCompact(entry.cachedTokens) ?? entry.cachedTokens} cached`
                        : ""}
                    </span>
                  </div>
                  {entry.firstDiffLine !== null ? (
                    <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
                      <div>First divergence at line {entry.firstDiffLine}:</div>
                      {entry.prevDivergence ? (
                        <div className="text-destructive truncate">- {entry.prevDivergence}</div>
                      ) : null}
                      {entry.currDivergence ? (
                        <div className="truncate text-success">+ {entry.currDivergence}</div>
                      ) : null}
                    </div>
                  ) : (
                    <div className="text-[11px] text-muted-foreground">
                      Identical prefix ({entry.commonPrefixChars} chars)
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-foreground">Model-swap cost simulator</h3>
            <p className="text-xs text-muted-foreground">
              Estimates this trace&apos;s {simulation.totals.llmCalls} LLM call
              {simulation.totals.llmCalls === 1 ? "" : "s"} (
              {formatCompact(simulation.totals.inputTokens) ?? simulation.totals.inputTokens} in /{" "}
              {formatCompact(simulation.totals.outputTokens) ?? simulation.totals.outputTokens} out)
              on other priced models.
            </p>
          </div>
          {simulation.totals.actualUsd !== null ? (
            <span className="text-xs font-medium text-foreground tabular">
              Actual: {formatCost(simulation.totals.actualUsd)}
            </span>
          ) : null}
        </div>
        {pricesQuery.isPending ? (
          <p className="text-xs text-muted-foreground">Loading price table…</p>
        ) : pricesQuery.isError ? (
          <p className="text-destructive text-xs">Couldn&apos;t load model prices.</p>
        ) : simulation.models.length === 0 ? (
          <p className="text-xs text-muted-foreground">No priced token usage on this trace yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-border text-muted-foreground">
                <tr>
                  <th className="py-1.5 pr-3 font-medium">Model</th>
                  <th className="py-1.5 pr-3 font-medium">Provider</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Est. cost</th>
                  <th className="py-1.5 text-right font-medium">Delta</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60 tabular">
                {simulation.models.slice(0, 12).map((row) => (
                  <tr key={`${row.provider}:${row.model}`}>
                    <td className="py-1.5 pr-3 font-mono text-[11px] text-foreground">
                      {row.model}
                    </td>
                    <td className="py-1.5 pr-3 text-muted-foreground">{row.provider}</td>
                    <td className="py-1.5 pr-3 text-right text-foreground">
                      {formatCost(row.estimatedUsd)}
                    </td>
                    <td
                      className={cn(
                        "py-1.5 text-right",
                        row.deltaUsd !== null && row.deltaUsd < 0
                          ? "text-success"
                          : "text-muted-foreground",
                      )}
                    >
                      {row.savingsPct !== null
                        ? `${row.savingsPct >= 0 ? "-" : "+"}${Math.abs(row.savingsPct).toFixed(0)}%`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
