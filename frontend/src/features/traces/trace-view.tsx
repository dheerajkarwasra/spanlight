import { getRouteApi } from "@tanstack/react-router";
import { Waypoints } from "lucide-react";
import { useId, useMemo, useRef, type MouseEvent } from "react";

import { EmptyState } from "@/components/empty-state";
import { Card } from "@/components/ui/card";
import type { Span, TraceDetail } from "@/lib/api";
import { useStoredChoice } from "@/lib/use-stored-choice";
import { cn } from "@/lib/utils";

import { SelectedSpanDetail } from "./selected-span-detail";
import { SpanDetailSkeleton } from "./span-detail-header";
import { buildSpanTree, findSpanNode } from "./span-tree";
import { SpanWaterfall } from "./span-waterfall";
import { TraceInfoPanel } from "./trace-info-panel";
import { useTraceInsights } from "./trace-queries";
import { TraceSummaryBar, TraceSummaryBarSkeleton } from "./trace-summary-bar";
import { INSPECTOR_PANE, OFFSETS, PANES, TREE_PANE, TWO_PANES_FROM } from "./trace-view-layout";
import { useNarrowerThan } from "./use-narrower-than";
import { usePageScrollPadding } from "./use-page-scroll-padding";
import { SpanWaterfallSkeleton } from "./waterfall-parts";

const traceRoute = getRouteApi("/_authed/$orgId/$projectId/traces/$traceId");

const TRACE_INFO_STATES = ["open", "closed"] as const;

/** The span and tab selected in the peek; "Open full page" carries them as `span=` / `tab=`. */
export interface PeekSelection {
  span?: string;
  tab?: string;
}

type CommonProps = Pick<TraceViewBodyProps, "trace" | "spans">;

/**
 * `page` keeps the span and tab in the URL and scrolls the window; `peek` gets them, its scroll
 * box (virtualizer, scroll padding) and the full-page address for Copy link from the sheet.
 */
export type TraceViewProps = CommonProps &
  (
    | { variant: "page" }
    | {
        variant: "peek";
        selection: PeekSelection;
        onSelectionChange: (next: PeekSelection) => void;
        scrollElement: HTMLElement | null;
        linkHref: string;
      }
  );

/** Figma "Trace detail" (spec §6): the summary bar, Trace info, then the tree and the inspector. */
export function TraceView(props: TraceViewProps) {
  return props.variant === "page" ? <PageTraceView {...props} /> : <TraceViewBody {...props} />;
}

function PageTraceView(props: CommonProps) {
  const search = traceRoute.useSearch();
  const navigate = traceRoute.useNavigate();
  return (
    <TraceViewBody
      {...props}
      variant="page"
      selection={{ span: search.span, tab: search.tab }}
      onSelectionChange={(next) => {
        void navigate({
          search: (prev) => ({ ...prev, ...next }),
          replace: true,
          resetScroll: false,
        });
      }}
    />
  );
}

interface TraceViewBodyProps {
  trace: TraceDetail;
  spans: Span[];
  variant: "page" | "peek";
  selection: PeekSelection;
  onSelectionChange: (next: PeekSelection) => void;
  scrollElement?: HTMLElement | null;
  linkHref?: string;
}

function TraceViewBody({
  trace,
  spans,
  variant,
  scrollElement = null,
  linkHref,
  selection,
  onSelectionChange,
}: TraceViewBodyProps) {
  const { span: spanId, tab } = selection;
  const onSelectSpan = (span: string) => {
    onSelectionChange({ ...selection, span });
  };
  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const [info, setInfo] = useStoredChoice("spanlight.trace-info", TRACE_INFO_STATES, "closed");
  const infoId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inspectorRef = useRef<HTMLDivElement>(null);
  // Stacked, the waterfall's rows scroll in their own box; side by side, the page or the peek does.
  const stacked = useNarrowerThan(rootRef, TWO_PANES_FROM);
  const insights = useTraceInsights(trace.trace_id);
  const barRef = useRef<HTMLDivElement>(null);
  usePageScrollPadding(barRef, variant === "page" ? document.documentElement : scrollElement);
  const selectedSpan = spans.find((span) => span.span_id === spanId) ?? roots[0]?.span ?? null;
  const offsets = OFFSETS[variant];

  /**
   * A row clicked or tapped with the panes stacked: bring the inspector up under the bar so the
   * selection shows. Arrow keys also select, but they leave the page where the focused row is.
   */
  function revealInspector(event: MouseEvent<HTMLDivElement>) {
    const clickedRow =
      event.target instanceof Element &&
      event.target.closest('[role="treeitem"]:not([aria-disabled="true"])');
    if (clickedRow && stacked) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      inspectorRef.current?.scrollIntoView({
        block: "start",
        behavior: reduce ? "auto" : "smooth",
      });
    }
  }

  return (
    <div ref={rootRef} className="@container flex min-w-0 flex-col gap-2">
      <TraceSummaryBar
        trace={trace}
        selectedSpan={selectedSpan}
        infoOpen={info === "open"}
        infoId={infoId}
        onInfoToggle={() => {
          setInfo(info === "open" ? "closed" : "open");
        }}
        className={offsets.bar}
        linkHref={linkHref}
        ref={barRef}
      />
      {info === "open" ? <TraceInfoPanel id={infoId} trace={trace} /> : null}
      {spans.length === 0 ? (
        <Card>
          <EmptyState
            icon={Waypoints}
            title="No spans in this trace"
            description="The trace was created but none of its spans have arrived. Spans are usually ingested within a few seconds."
          />
        </Card>
      ) : (
        <div className={PANES}>
          {/* Row clicks bubble here; keyboard moves in the tree don't scroll the page away. */}
          <div className={TREE_PANE} onClick={revealInspector}>
            <SpanWaterfall
              spans={spans}
              roots={roots}
              selectedSpanId={selectedSpan?.span_id ?? null}
              onSelect={onSelectSpan}
              insights={insights}
              scrollElement={scrollElement}
              capRows={stacked}
              stickyBar={barRef}
            />
          </div>
          <div ref={inspectorRef} className={cn(INSPECTOR_PANE, offsets.inspector)}>
            {selectedSpan ? (
              <SelectedSpanDetail
                key={selectedSpan.span_id}
                span={selectedSpan}
                tab={tab}
                onTabChange={(next) => {
                  onSelectionChange({ ...selection, tab: next });
                }}
                inTrace={{
                  traceId: trace.trace_id,
                  childNodes: findSpanNode(roots, selectedSpan.span_id)?.children ?? [],
                  onSelectSpan,
                }}
              />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

/** The view's shape while the trace loads. */
export function TraceViewSkeleton() {
  return (
    <div className="@container flex min-w-0 flex-col gap-2">
      <TraceSummaryBarSkeleton />
      <div className={PANES}>
        <SpanWaterfallSkeleton />
        <SpanDetailSkeleton />
      </div>
    </div>
  );
}
