import { getRouteApi, Link } from "@tanstack/react-router";
import { GitCompareArrows } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import { isApiError, type TraceComparison, type TraceSummary } from "@/lib/api";
import { formatTimestamp, pluralize, shortId } from "@/lib/format";

import { CompareSteps } from "./compare-steps";
import { CompareTotals } from "./compare-totals";
import { TracesBreadcrumbs } from "./trace-header";
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
        <TracesBreadcrumbs current="Compare" />
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
  const { a, b } = comparison;
  return (
    <>
      <div className="grid gap-3 md:grid-cols-2">
        <TraceCard label="A · Baseline" trace={a} />
        <TraceCard label="B · Candidate" trace={b} />
      </div>
      <CompareTotals comparison={comparison} />
      {comparison.truncated ? (
        <Notice tone="warning" role="status">
          A trace has more than 2,000 spans: only the first 2,000 of each are compared step by step.
          Duration, tokens, cost and errors above cover every span; the step counts don&apos;t.
        </Notice>
      ) : null}
      <CompareSteps comparison={comparison} />
    </>
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
