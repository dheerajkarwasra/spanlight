import { Info } from "lucide-react";
import { useId } from "react";

import { UnknownValue } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { formatCost, pluralize } from "@/lib/format";
import { RETRY_NO_KNOWN_COST_REASON, RETRY_NOT_COMPUTED_REASON } from "@/lib/retry-reasons";

import type { RetrySummary } from "./providers-format";

interface RetryCostTileProps {
  summary: RetrySummary;
  /** The API's `retry_cost_note`: how retries are attributed. */
  note: string;
}

/**
 * Figma "Retry cost tile": what the window's retried calls cost, every provider added up. The
 * total is "—" with its reason when a trace predates retry tracking or a retried call is unpriced.
 */
export function RetryCostTile({ summary, note }: RetryCostTileProps) {
  const titleId = useId();
  const notComputed = summary.reason === RETRY_NOT_COMPUTED_REASON;

  return (
    <Card role="region" aria-labelledby={titleId} className="flex min-w-0 flex-col gap-1.5 p-5">
      <div className="flex items-center gap-1.5">
        <h2 id={titleId} className="text-xs font-medium text-muted-foreground">
          Retry cost
        </h2>
        <Tooltip content={note}>
          <button
            type="button"
            aria-label="How retries are counted"
            className="relative inline-flex size-4 items-center justify-center rounded-full text-muted-foreground after:absolute after:-inset-3.5"
          >
            <Info aria-hidden className="size-3.5" />
          </button>
        </Tooltip>
      </div>
      <p className="text-metric text-foreground tabular">
        {summary.cost === null ? (
          <UnknownValue reason={summary.reason ?? RETRY_NOT_COMPUTED_REASON} />
        ) : (
          formatCost(summary.cost)
        )}
      </p>
      <p className="text-xs font-medium text-muted-foreground">
        {summary.calls === null ? (
          <>
            Retried calls: <UnknownValue reason={RETRY_NOT_COMPUTED_REASON} />
          </>
        ) : (
          `${pluralize(summary.calls, "retried call")} in the window`
        )}
      </p>
      <p className="text-xs font-medium text-subtle-foreground">
        {notComputed ? (
          "Older traces are recomputed when a span of theirs is ingested again."
        ) : (
          <>
            <span className="max-sm:hidden">
              Earlier attempts of the same request inside one trace.{" "}
            </span>
            {summary.breakdown.map((entry, index) => (
              <span key={entry.label}>
                {index > 0 ? " · " : null}
                {entry.label}{" "}
                {entry.cost === null ? (
                  <UnknownValue reason={RETRY_NO_KNOWN_COST_REASON} />
                ) : (
                  formatCost(entry.cost)
                )}
              </span>
            ))}
          </>
        )}
      </p>
    </Card>
  );
}
