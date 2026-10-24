import { getRouteApi, Link } from "@tanstack/react-router";
import { SearchX } from "lucide-react";
import { useEffect } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useProjectParams } from "@/features/shell/project-context";
import { isApiError } from "@/lib/api";

import { recallTracesSearch, rememberOpenedTrace } from "./last-traces-search";
import { useTraceQuery } from "./trace-queries";
import { TracesBreadcrumbs } from "./trace-header";
import { TraceView, TraceViewSkeleton } from "./trace-view";

const traceRoute = getRouteApi("/_authed/$orgId/$projectId/traces/$traceId");

/** Tight gaps above the tree so its first row sits ≤ 160 px under the breadcrumbs (spec §1). */
const PAGE_CLASSES = "flex flex-col gap-1";

/** The trace page: breadcrumbs over the trace view, which holds the summary bar, tree and inspector. */
export function TraceDetailPage() {
  const { traceId } = traceRoute.useParams();
  const traceQuery = useTraceQuery(traceId);

  useEffect(() => {
    rememberOpenedTrace(traceId);
  }, [traceId]);

  if (traceQuery.isPending) {
    return (
      <div className={PAGE_CLASSES} aria-busy>
        <span className="sr-only" role="status">
          Loading trace
        </span>
        <TracesBreadcrumbs current="Trace" />
        <TraceViewSkeleton />
      </div>
    );
  }

  if (traceQuery.isError) {
    if (isApiError(traceQuery.error) && traceQuery.error.isNotFound) {
      return <TraceNotFound />;
    }
    return (
      <div className={PAGE_CLASSES}>
        <TracesBreadcrumbs current="Trace" />
        <Card>
          <ErrorState
            error={traceQuery.error}
            title="Couldn't load this trace"
            onRetry={() => {
              void traceQuery.refetch();
            }}
          />
        </Card>
      </div>
    );
  }

  return (
    <div className={PAGE_CLASSES}>
      <TracesBreadcrumbs current={traceQuery.data.name ?? "Unnamed trace"} />
      <TraceView trace={traceQuery.data} spans={traceQuery.data.spans} variant="page" />
    </div>
  );
}

function TraceNotFound() {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className={PAGE_CLASSES}>
      <TracesBreadcrumbs current="Trace" />
      <Card>
        <EmptyState
          icon={SearchX}
          title="Trace not found"
          description="It may have been deleted by the retention policy, or the ID is wrong."
          action={
            <Button asChild size="sm">
              <Link
                to="/$orgId/$projectId/traces"
                params={{ orgId, projectId }}
                search={recallTracesSearch()}
              >
                Back to traces
              </Link>
            </Button>
          }
        />
      </Card>
    </div>
  );
}
