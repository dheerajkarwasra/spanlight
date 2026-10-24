import { ErrorState } from "@/components/error-state";
import { Card } from "@/components/ui/card";
import type { Span } from "@/lib/api";

import type { InspectorTab } from "./inspector-tabs";
import { SpanDetailSkeleton } from "./span-detail-header";
import { SpanDetailPanel, type SpanInTrace } from "./span-detail-panel";
import { useSpanQuery } from "./trace-queries";

interface SelectedSpanDetailProps {
  span: Span;
  tab: string | undefined;
  onTabChange: (tab: InspectorTab) => void;
  inTrace: SpanInTrace;
}

/**
 * The selected span's inspector. A span the trace detail returned without its payloads (the
 * trace's payloads passed the response budget) is fetched whole first.
 */
export function SelectedSpanDetail({ span, tab, onTabChange, inTrace }: SelectedSpanDetailProps) {
  const fullSpan = useSpanQuery(inTrace.traceId, span);
  if (span.payload_omitted && fullSpan.isPending) {
    return <SpanDetailSkeleton />;
  }
  if (span.payload_omitted && fullSpan.isError) {
    return (
      <Card>
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
      inTrace={inTrace}
    />
  );
}
