import { Link } from "@tanstack/react-router";
import { Check, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { UnknownValue } from "@/components/unknown-value";
import { Skeleton } from "@/components/ui/skeleton";
import { useTraceScoresQuery } from "@/features/scores";
import { useProjectParams } from "@/features/shell";
import type { ScoreConfig } from "@/lib/api";
import { formatScoreValue } from "@/lib/score-format";

import { agreementFor, verdictText, type ScoreValue } from "./agreement";

interface LastItemStripProps {
  configs: readonly ScoreConfig[];
  last: { traceId: string; values: Record<string, ScoreValue> };
}

/**
 * Figma "Review work mode", Last item: after a submit, how the reviewer's scores for that item
 * compare with the evaluator's. Shown only once the item is submitted, so reviews stay blind.
 */
export function LastItemStrip({ configs, last }: LastItemStripProps) {
  const { orgId, projectId } = useProjectParams();
  const scores = useTraceScoresQuery(last.traceId);
  return (
    <div role="status" className="flex flex-col gap-2 rounded-input bg-surface-muted px-3.5 py-3">
      <div className="flex items-center gap-2">
        <span className="flex-1 text-2xs font-semibold tracking-[0.06em] text-muted-foreground uppercase">
          Last item
        </span>
        <Link
          to="/$orgId/$projectId/traces/$traceId"
          params={{ orgId, projectId, traceId: last.traceId }}
          className="inline-flex min-h-6 items-center rounded-sm text-label font-semibold text-accent hover:underline"
        >
          Open trace
        </Link>
      </div>
      {scores.data !== undefined ? (
        <ul className="flex flex-col gap-1.5">
          {agreementFor(configs, last.values, scores.data).map((row, index) => {
            // One row per config, in the same order.
            const type = configs[index]?.data_type ?? null;
            const value = (score: ScoreValue) => formatScoreValue(score, type);
            return (
              <li key={row.name} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="min-w-0 flex-1 text-label font-medium [overflow-wrap:anywhere] text-foreground">
                  {row.name} · you{" "}
                  {row.you === null ? (
                    <UnknownValue reason="You didn't score this." />
                  ) : (
                    value(row.you)
                  )}{" "}
                  ·{" "}
                  {row.evaluator === null ? (
                    <span className="text-muted-foreground">No evaluator score</span>
                  ) : (
                    `evaluator ${value(row.evaluator)}`
                  )}
                </span>
                {row.verdict === "none" ? null : (
                  <Badge size="sm" variant={row.verdict === "agree" ? "success" : "warning"}>
                    {row.verdict === "agree" ? (
                      <Check aria-hidden />
                    ) : (
                      <TriangleAlert aria-hidden />
                    )}
                    {verdictText(row)}
                  </Badge>
                )}
              </li>
            );
          })}
        </ul>
      ) : scores.isPending ? (
        <Skeleton className="h-4 w-3/4 rounded-full" />
      ) : (
        <p className="text-label text-muted-foreground">Couldn't load evaluator scores.</p>
      )}
    </div>
  );
}
