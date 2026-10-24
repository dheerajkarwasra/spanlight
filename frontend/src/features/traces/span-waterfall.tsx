import { useRef, useState, type CSSProperties, type KeyboardEvent, type RefObject } from "react";

import { Card } from "@/components/ui/card";
import type { Insight, Span } from "@/lib/api";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import { computeTimeline, timelineTicks } from "./span-timeline";
import { failedSpanCount, kindsInTrace, type SpanNode } from "./span-tree";
import { treeKeyAction } from "./tree-keys";
import { useTreeVirtualizer } from "./use-tree-virtualizer";
import { useWaterfallTree } from "./use-waterfall-tree";
import type { WaterfallItem } from "./waterfall-items";
import { GhostRow, TimelineAxis, WaterfallLegend } from "./waterfall-parts";
import { WaterfallRow } from "./waterfall-row";
import { WaterfallToolbar } from "./waterfall-toolbar";

/** Fewer axis ticks on a phone, so their labels never collide in the narrower timeline. */
const PHONE_QUERY = "(max-width: 639px)";
const PHONE_MAX_TICKS = 3;
/** Above this many visible rows only the rows in view (plus overscan) are rendered. */
const VIRTUALIZE_ROWS = 300;

interface SpanWaterfallProps {
  spans: Span[];
  roots: SpanNode[];
  selectedSpanId: string | null;
  onSelect: (spanId: string) => void;
  /** The trace's open and acknowledged Doctor findings: the span Doctor reads gets a flag. */
  insights?: readonly Insight[];
  /** The box that scrolls the tree (the peek sheet); the window when absent. */
  scrollElement?: HTMLElement | null;
  /**
   * Stacked trace view (phone): the rows scroll in their own 220 px box under the toolbar, so the
   * inspector stays close and the toolbar never eats the rows' height.
   */
  capRows?: boolean;
  /** The page's sticky bar over the tree (the summary bar), which scrolled-to rows clear. */
  stickyBar?: RefObject<HTMLElement | null>;
  /** Open with spans from this many levels down folded (session turns show 3). */
  maxDepth?: number;
}

/** Figma "Spans" card: the span tree as a keyboard-navigable ARIA tree with timeline bars. */
export function SpanWaterfall(props: SpanWaterfallProps) {
  const { spans, roots, selectedSpanId, onSelect, insights = [] } = props;
  const view = useWaterfallTree(props);
  const { rows, items, matches } = view;
  const timeline = computeTimeline(spans);
  const phone = useMediaQuery(PHONE_QUERY);
  const ticks = timelineTicks(timeline.durationMs, phone ? PHONE_MAX_TICKS : undefined);
  const failed = failedSpanCount(spans);
  const doctor = insights.find((insight) => insight.severity === "critical") ?? insights[0];
  const focusableId = rows.some((row) => row.span.span_id === selectedSpanId)
    ? selectedSpanId
    : (rows[0]?.span.span_id ?? null);
  const matchRows = rows.filter((row) => matches.has(row.span.span_id));
  const matchIndex = matchRows.findIndex((row) => row.span.span_id === selectedSpanId);

  const virtual = items.length > VIRTUALIZE_ROWS;
  const treeRef = useRef<HTMLDivElement>(null);
  const [rowsBox, setRowsBox] = useState<HTMLDivElement | null>(null);
  const { virtualizer, scrollMargin, focusRow } = useTreeVirtualizer({
    count: items.length,
    enabled: virtual,
    indexOf: (spanId) =>
      items.findIndex((item) => item.type === "span" && item.row.span.span_id === spanId),
    focusableId,
    selectedSpanId,
    onSelect,
    treeRef,
    scrollElement: props.capRows ? rowsBox : props.scrollElement,
    stickyBar: props.stickyBar,
  });

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = rows.findIndex((row) => row.span.span_id === focusableId);
    const action = treeKeyAction(rows, index, event.key);
    if (action === undefined) {
      return;
    }
    event.preventDefault();
    if (action && "focus" in action) {
      focusRow(action.focus);
    } else if (action) {
      view.setExpanded(action.toggle, action.expanded);
    }
  }

  function renderItem(item: WaterfallItem, style?: CSSProperties) {
    // Top-level rows count the ghost rows beside them in their set.
    const top = view.topLevel.get(item);
    const position = top === undefined ? null : { posInSet: top, setSize: view.topLevel.size };
    if (item.type === "ghost") {
      return (
        <GhostRow
          key={`ghost-${item.parentSpanId}`}
          {...item}
          style={style}
          posInSet={position?.posInSet}
          setSize={position?.setSize}
        />
      );
    }
    const { row } = item;
    const id = row.span.span_id;
    const meta = view.meta.get(id);
    return (
      <WaterfallRow
        key={id}
        row={row}
        node={view.nodes.get(id)}
        meta={meta && position ? { ...meta, ...position } : meta}
        style={style}
        timeline={timeline}
        ticks={ticks}
        selected={id === selectedSpanId}
        focusable={id === focusableId}
        dimmed={
          (view.kind !== null && row.span.kind !== view.kind) ||
          (view.query.trim() !== "" && !matches.has(id))
        }
        doctor={id === view.doctorRowId ? doctor : undefined}
        onSelect={() => {
          onSelect(id);
        }}
        onToggle={() => {
          view.setExpanded(id, !row.expanded);
        }}
      />
    );
  }

  return (
    <Card className="@container flex min-w-0 flex-col gap-1 px-4 pt-2 pb-4">
      <WaterfallToolbar
        spanCount={spans.length}
        failedCount={failed}
        query={view.query}
        onQueryChange={view.setQuery}
        onNextMatch={() => {
          const next = matchRows[(matchIndex + 1) % matchRows.length];
          if (next) {
            onSelect(next.span.span_id);
          }
        }}
        matchIndex={matchIndex + 1}
        matchCount={matchRows.length}
        kinds={kindsInTrace(spans)}
        kind={view.kind}
        onKindChange={view.setKind}
        collapseRetries={view.collapseRetries}
        onCollapseRetriesChange={view.setCollapseRetries}
        onExpandAll={view.parents.length > 0 ? view.expandAll : null}
        onCollapseAll={() => {
          view.collapseAll();
          const firstRoot = roots[0];
          if (firstRoot) {
            onSelect(firstRoot.span.span_id);
          }
        }}
      />

      <div
        ref={setRowsBox}
        data-slot="tree-rows"
        className={cn(props.capRows && "-mx-1 max-h-[220px] scroll-pt-5 overflow-auto px-1")}
      >
        <div className="flex flex-col gap-0.5">
          <TimelineAxis ticks={ticks} sticky={Boolean(props.capRows)} />
          <div
            ref={treeRef}
            role="tree"
            aria-label="Spans"
            onKeyDown={handleKeyDown}
            className={virtual ? "relative" : "flex flex-col gap-0.5"}
            style={virtual ? { height: virtualizer.getTotalSize() } : undefined}
          >
            {virtual
              ? virtualizer.getVirtualItems().map((virtualItem) => {
                  const item = items[virtualItem.index];
                  return item
                    ? renderItem(item, {
                        position: "absolute",
                        insetInline: 0,
                        top: 0,
                        transform: `translateY(${virtualItem.start - scrollMargin}px)`,
                      })
                    : null;
                })
              : items.map((item) => renderItem(item))}
          </div>
        </div>
      </div>

      <WaterfallLegend spans={spans} />
    </Card>
  );
}
