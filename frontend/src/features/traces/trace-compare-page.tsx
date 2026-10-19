import { getRouteApi, Link } from "@tanstack/react-router";
import { GitCompareArrows } from "lucide-react";
import type { ReactNode } from "react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { CompareTile } from "@/features/experiments/compare-tiles";
import {
  formatLatency,
  formatMoneyDelta,
  formatMsDelta,
} from "@/features/experiments/compare-view";
import { useProjectParams } from "@/features/shell/project-context";
import { isApiError, type TraceComparison, type TraceSummary } from "@/lib/api";
import { formatInteger, formatTimestamp, pluralize, shortId } from "@/lib/format";

import { CompareSteps } from "./compare-steps";
import { BackToTracesLink } from "./trace-header";
import { useTraceComparisonQuery } from "./trace-queries";

const route = getRouteApi("/_authed/$orgId/$projectId/traces/compare");

/** Figma "Traces — Compare": two traces of the same code, step by step. */
export function TraceComparePage() {
  const { a, b } = route.useSearch();
  const comparison = useTraceComparisonQuery(a, b);

  function body(): ReactNode {
    if (a === undefined || b === undefined || a === b) {
      return (
        <EmptyState
          icon={GitCompareArrows}
          title="Pick two traces to compare"
          description={
            a !== undefined && a === b
              ? "Baseline and candidate are the same trace. Pick two different traces."
              : "Tick two traces in the trace list, then choose Compare. The earlier one is the baseline."
          }
          className="rounded-card bg-surface-muted"
        />
      );
    }
    if (comparison.isError) {
      return (
        <ErrorState
          error={comparison.error}
          title={
            isApiError(comparison.error) && comparison.error.isNotFound
              ? "These traces aren't in this project"
              : "Couldn't compare these traces"
          }
          onRetry={() => void comparison.refetch()}
        />
      );
    }
    if (!comparison.data) {
      return (
        <div role="status" aria-label="Comparing traces" className="flex flex-col gap-4">
          <Skeleton className="h-28 rounded-card" />
          <Skeleton className="h-24 rounded-card" />
          <Skeleton className="h-96 rounded-card" />
        </div>
      );
    }
    return <Comparison comparison={comparison.data} />;
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <BackToTracesLink />
        <PageHeader
          title="Compare traces"
          description="Steps are matched by name and order. Lower is better for time, tokens and cost."
        />
      </div>
      {body()}
    </div>
  );
}

function Comparison({ comparison }: { comparison: TraceComparison }) {
  const { a, b, totals } = comparison;
  return (
    <>
      <div className="grid gap-3 md:grid-cols-2">
        <TraceCard label="A · Baseline" trace={a} />
        <TraceCard label="B · Candidate" trace={b} />
      </div>
      <section aria-labelledby="compare-totals">
        <h2 id="compare-totals" className="sr-only">
          Totals
        </h2>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
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
        </dl>
      </section>
      {comparison.truncated ? (
        <Notice tone="warning" role="status">
          A trace has more than 2,000 spans: only the first 2,000 of each are compared step by step.
          The totals above cover every span.
        </Notice>
      ) : null}
      <CompareSteps comparison={comparison} />
    </>
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

function TraceCard({ label, trace }: { label: string; trace: TraceSummary }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-overline text-subtle-foreground uppercase">{label}</p>
        <p className="truncate text-base font-semibold text-foreground">
          {trace.name ?? <span className="text-muted-foreground">Unnamed trace</span>}
        </p>
        <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
          <span className="font-mono">{shortId(trace.trace_id, 12)}</span>
          <span aria-hidden>·</span>
          <span>Started {formatTimestamp(trace.started_at)}</span>
          <span aria-hidden>·</span>
          <span>{pluralize(trace.span_count, "span")}</span>
        </p>
      </div>
      <Button variant="secondary" size="sm" className="self-start sm:self-center" asChild>
        <Link
          to="/$orgId/$projectId/traces/$traceId"
          params={{ orgId, projectId, traceId: trace.trace_id }}
          aria-label={`Open trace ${label.charAt(0)}`}
        >
          Open trace
        </Link>
      </Button>
    </Card>
  );
}
