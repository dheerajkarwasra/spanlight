import type { Span } from "@/lib/api";

import type { InspectorTab } from "./inspector-tabs";
import { SpanDetailPanel, type SpanContentState, type SpanInTrace } from "./span-detail-panel";
import { useSpanQuery } from "./trace-queries";

interface SelectedSpanDetailProps {
  span: Span;
  tab: string | undefined;
  onTabChange: (tab: InspectorTab) => void;
  inTrace: SpanInTrace;
}

/**
 * The selected span's inspector. A span the trace detail returned without its attributes or
 * payloads (the trace's stored content passed the response budget) is fetched whole; the header
 * and tabs stay while that loads (Figma S9 states), the content tabs show the retrieval.
 */
export function SelectedSpanDetail({ span, tab, onTabChange, inTrace }: SelectedSpanDetailProps) {
  const incomplete = span.payload_omitted || span.attributes_omitted;
  const fullSpan = useSpanQuery(inTrace.traceId, span, incomplete);
  let content: SpanContentState | undefined;
  if (incomplete && fullSpan.isPending) {
    content = { status: "pending" };
  } else if (incomplete && fullSpan.isError && fullSpan.data === undefined) {
    // A failed background refetch keeps the span already shown.
    content = {
      status: "error",
      error: fullSpan.error,
      onRetry: () => {
        void fullSpan.refetch();
      },
    };
  }
  return (
    <SpanDetailPanel
      span={fullSpan.data ?? span}
      tab={tab}
      onTabChange={onTabChange}
      inTrace={inTrace}
      content={content}
    />
  );
}
