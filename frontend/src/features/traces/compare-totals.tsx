import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { CompareTile } from "@/features/experiments/compare-tiles";
import {
  formatLatency,
  formatMoneyDelta,
  formatMsDelta,
} from "@/features/experiments/compare-view";
import type { TraceComparison, TraceSummary } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { onlyInCounts } from "./compare-counts";

/** The compare page's totals row: A → B per measure, then the steps only one trace has (C4). */
export function CompareTotals({ comparison }: { comparison: TraceComparison }) {
  const { a, b, totals } = comparison;
  const onlyIn = onlyInCounts(comparison.steps);
  return (
    <section aria-labelledby="compare-totals">
      <h2 id="compare-totals" className="sr-only">
        Totals
      </h2>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
        <CompareTile
          label="Duration"
          before={formatLatency(a.duration_ms)}
          after={formatLatency(b.duration_ms)}
          change={{
            delta: totals.duration_ms,
            text: formatMsDelta(totals.duration_ms),
            words: ["slower", "faster"],
          }}
        />
        <CompareTile
          label="Input tokens"
          before={formatInteger(a.input_tokens)}
          after={formatInteger(b.input_tokens)}
          change={countChange(totals.input_tokens, ["more", "fewer"])}
        />
        <CompareTile
          label="Output tokens"
          before={formatInteger(a.output_tokens)}
          after={formatInteger(b.output_tokens)}
          change={countChange(totals.output_tokens, ["more", "fewer"])}
        />
        <CompareTile
          label="Cost"
          before={cost(a)}
          after={cost(b)}
          change={{
            delta: totals.cost_usd === null ? null : Number(totals.cost_usd),
            text: formatMoneyDelta(totals.cost_usd),
            words: ["costs more", "costs less"],
          }}
        />
        <CompareTile
          label="Errors"
          before={formatInteger(a.error_count)}
          after={formatInteger(b.error_count)}
          change={countChange(
            totals.error_count,
            Math.abs(totals.error_count) === 1
              ? ["more error", "fewer error"]
              : ["more errors", "fewer errors"],
          )}
        />
        <div className="flex min-w-0 flex-col gap-1 rounded-tile border border-border bg-surface p-4">
          <dt className="text-overline text-subtle-foreground uppercase">Steps</dt>
          <dd className="text-sm text-foreground">
            Only in A: <span className="font-mono font-bold">{formatInteger(onlyIn.a)}</span>
            <span aria-hidden> · </span>
            <span className="sr-only">, </span>
            only in B: <span className="font-mono font-bold">{formatInteger(onlyIn.b)}</span>
          </dd>
          {comparison.truncated ? (
            // The steps stop at 2,000 spans per trace; the other tiles cover every span.
            <dd className="text-xs text-muted-foreground">Of the first 2,000 steps</dd>
          ) : null}
        </div>
      </dl>
    </section>
  );
}

function countChange(delta: number, words: [string, string]) {
  return {
    delta,
    text: `${delta > 0 ? "+" : "−"}${formatInteger(Math.abs(delta))}`,
    words,
  };
}

function cost(trace: TraceSummary) {
  return (
    <CostValue
      cost={trace.cost_usd}
      hasUnpriced={trace.has_unpriced}
      unknownReason={UNPRICED_OR_NO_USAGE_REASON}
    />
  );
}
