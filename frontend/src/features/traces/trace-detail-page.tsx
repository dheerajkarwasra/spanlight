import { getRouteApi, Link } from "@tanstack/react-router";
import { SearchX, Waypoints } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScoresPanel } from "@/features/scores";
import { useProjectParams } from "@/features/shell/project-context";
import { isApiError, type Span, type TraceDetail } from "@/lib/api";

import { hasModelCall } from "./cost-explanation";
import { recallTracesSearch, rememberOpenedTrace } from "./last-traces-search";
import { SpanDetailPanel, SpanDetailSkeleton, type SpanDetailTab } from "./span-detail-panel";
import { buildSpanTree } from "./span-tree";
import { SpanCostCard } from "./span-cost-card";
import { SpanUsageCard, SpanUsageSkeleton } from "./span-usage-card";
import { SpanWaterfall, SpanWaterfallSkeleton } from "./span-waterfall";
import { useSpanQuery, useTraceQuery } from "./trace-queries";
import { BackToTracesLink, TraceHeader, TraceHeaderSkeleton } from "./trace-header";

const traceRoute = getRouteApi("/_authed/$orgId/$projectId/traces/$traceId");

const PAGE_CLASSES = "flex flex-col gap-5";

/** The span detail stays in view (scrolling on its own) while a long waterfall scrolls past. */
const DETAIL_VIEWPORT_HEIGHT = "xl:max-h-[calc(100dvh-3rem)] xl:overflow-y-auto";

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
        <BackToTracesLink />
        <TraceHeaderSkeleton />
        <TraceBodyLayout
          left={
            <>
              <SpanWaterfallSkeleton />
              <SpanUsageSkeleton />
            </>
          }
          right={<SpanDetailSkeleton />}
        />
      </div>
    );
  }

  if (traceQuery.isError) {
    if (isApiError(traceQuery.error) && traceQuery.error.isNotFound) {
      return <TraceNotFound />;
    }
    return (
      <div className={PAGE_CLASSES}>
        <BackToTracesLink />
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
      <BackToTracesLink />
      <TraceHeader trace={traceQuery.data} />
      <TraceBody trace={traceQuery.data} />
    </div>
  );
}

function TraceBody({ trace }: { trace: TraceDetail }) {
  const search = traceRoute.useSearch();
  const navigate = traceRoute.useNavigate();
  const [tab, setTab] = useState<SpanDetailTab>("io");
  const roots = buildSpanTree(trace.spans);

  if (trace.spans.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={Waypoints}
          title="No spans in this trace"
          description="The trace was created but none of its spans have arrived. Spans are usually ingested within a few seconds."
        />
      </Card>
    );
  }

  const selectedSpan =
    trace.spans.find((span) => span.span_id === search.span) ?? roots[0]?.span ?? null;

  function selectSpan(spanId: string) {
    void navigate({
      search: (prev) => ({ ...prev, span: spanId }),
      replace: true,
      resetScroll: false,
    });
  }

  return (
    <TraceBodyLayout
      left={
        <>
          <SpanWaterfall
            spans={trace.spans}
            roots={roots}
            selectedSpanId={selectedSpan?.span_id ?? null}
            onSelect={selectSpan}
          />
          {selectedSpan ? <SpanUsageCard span={selectedSpan} /> : null}
          {selectedSpan && hasModelCall(selectedSpan) ? (
            <SpanCostCard key={selectedSpan.span_id} span={selectedSpan} traceId={trace.trace_id} />
          ) : null}
          <ScoresPanel traceId={trace.trace_id} />
        </>
      }
      right={
        selectedSpan ? (
          <SelectedSpanDetail
            key={selectedSpan.span_id}
            span={selectedSpan}
            tab={tab}
            onTabChange={setTab}
            traceId={trace.trace_id}
          />
        ) : null
      }
    />
  );
}

/**
 * The selected span's detail. A span trace detail returned without its payloads (the trace's
 * payloads passed the response budget) is fetched whole first.
 */
function SelectedSpanDetail({
  span,
  tab,
  onTabChange,
  traceId,
}: {
  span: Span;
  tab: SpanDetailTab;
  onTabChange: (tab: SpanDetailTab) => void;
  traceId: string;
}) {
  const fullSpan = useSpanQuery(traceId, span);
  if (span.payload_omitted && fullSpan.isPending) {
    return <SpanDetailSkeleton className={DETAIL_VIEWPORT_HEIGHT} />;
  }
  if (span.payload_omitted && fullSpan.isError) {
    return (
      <Card className={DETAIL_VIEWPORT_HEIGHT}>
        <ErrorState
          error={fullSpan.error}
          title="Couldn't load this span"
          onRetry={() => {
            void fullSpan.refetch();
          }}
        />
      </Card>
    );
  }
  return (
    <SpanDetailPanel
      span={fullSpan.data ?? span}
      tab={tab}
      onTabChange={onTabChange}
      traceId={traceId}
      className={DETAIL_VIEWPORT_HEIGHT}
    />
  );
}

/**
 * Figma "Body": spans and usage on the left (622), the span detail on the right (438).
 * Below 1280px the columns stack.
 */
function TraceBodyLayout({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,622fr)_minmax(0,438fr)] xl:items-start">
      <div className="flex min-w-0 flex-col gap-6">{left}</div>
      <div className="min-w-0 xl:sticky xl:top-6">{right}</div>
    </div>
  );
}

function TraceNotFound() {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className={PAGE_CLASSES}>
      <BackToTracesLink />
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
