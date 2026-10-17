import { ChevronDown, CircleAlert } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Span } from "@/lib/api";
import { formatDuration, formatInteger } from "@/lib/format";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import { SpanKindIcon } from "./span-kind";
import { SPAN_KIND_META } from "./span-kind-meta";
import {
  ancestorIds,
  barLayout,
  computeTimeline,
  failedSpanCount,
  flattenSpanTree,
  formatTickLabel,
  kindsInTrace,
  parentSpanIds,
  timelineTicks,
  ttftOffsetPercent,
  type SpanNode,
  type SpanRow,
  type Tick,
  type Timeline,
} from "./span-tree";

/**
 * Name column | timeline | duration (Figma "Traces/Span row": 244 / 262 / 60 at 582px). Shared
 * by the time axis and every row so the ticks line up with the bars. Phones get a narrower
 * duration column and fewer ticks, so the whole timeline fits the card and every bar shows
 * without scrolling sideways.
 */
const ROW_GRID =
  "grid grid-cols-[minmax(0,42%)_minmax(0,1fr)_3.25rem] items-center gap-2 sm:grid-cols-[minmax(0,42%)_minmax(0,1fr)_3.75rem]";
/** Below this width the ticks would overlap, so only the narrowest screens scroll sideways. */
const MIN_WATERFALL_WIDTH = "min-w-[17rem]";
/** Fewer axis ticks on a phone, so their labels never collide in the narrower timeline. */
const PHONE_QUERY = "(max-width: 639px)";
const PHONE_MAX_TICKS = 3;
/** Figma: name cell padding-left = 8px + 18px per depth level. */
const BASE_INDENT_PX = 8;
const INDENT_PER_LEVEL_PX = 18;

/** Error bars glow softly in the danger colour. */
const ERROR_BAR_GLOW = "shadow-[0_0_5px_color-mix(in_srgb,var(--danger)_55%,transparent)]";

function spanRowId(spanId: string): string {
  return `span-row-${spanId}`;
}

interface SpanWaterfallProps {
  spans: Span[];
  roots: SpanNode[];
  selectedSpanId: string | null;
  onSelect: (spanId: string) => void;
}

/** Figma "Spans" card: the span tree as a keyboard-navigable ARIA tree with timeline bars. */
export function SpanWaterfall({ spans, roots, selectedSpanId, onSelect }: SpanWaterfallProps) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());

  // A selected span (e.g. from the URL) is always revealed, even inside a collapsed parent.
  const hiddenAncestors = new Set(selectedSpanId ? ancestorIds(spans, selectedSpanId) : []);
  const effectiveCollapsed = new Set([...collapsed].filter((id) => !hiddenAncestors.has(id)));
  const rows = flattenSpanTree(roots, effectiveCollapsed);
  const timeline = computeTimeline(spans);
  const phone = useMediaQuery(PHONE_QUERY);
  const ticks = timelineTicks(timeline.durationMs, phone ? PHONE_MAX_TICKS : undefined);
  const parents = parentSpanIds(roots);
  const failed = failedSpanCount(spans);
  const focusableId = rows.some((row) => row.span.span_id === selectedSpanId)
    ? selectedSpanId
    : (rows[0]?.span.span_id ?? null);

  function setExpanded(spanId: string, expanded: boolean) {
    const next = new Set(effectiveCollapsed);
    if (expanded) {
      next.delete(spanId);
    } else {
      next.add(spanId);
      // Collapsing an ancestor of the selection moves the selection up to it.
      if (hiddenAncestors.has(spanId)) {
        onSelect(spanId);
      }
    }
    setCollapsed(next);
  }

  function focusRow(spanId: string) {
    onSelect(spanId);
    document.getElementById(spanRowId(spanId))?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = rows.findIndex((row) => row.span.span_id === focusableId);
    const row = rows[index];
    if (!row) {
      return;
    }
    let target: SpanRow | undefined;
    switch (event.key) {
      case "ArrowDown":
        target = rows[index + 1];
        break;
      case "ArrowUp":
        target = rows[index - 1];
        break;
      case "Home":
        target = rows[0];
        break;
      case "End":
        target = rows.at(-1);
        break;
      case "ArrowRight":
        if (row.hasChildren && !row.expanded) {
          setExpanded(row.span.span_id, true);
        } else if (row.hasChildren) {
          target = rows[index + 1];
        }
        break;
      case "ArrowLeft":
        if (row.hasChildren && row.expanded) {
          setExpanded(row.span.span_id, false);
        } else if (row.span.parent_span_id !== null) {
          const parentId = row.span.parent_span_id;
          target = rows.find((candidate) => candidate.span.span_id === parentId);
        }
        break;
      default:
        return;
    }
    event.preventDefault();
    if (target) {
      focusRow(target.span.span_id);
    }
  }

  return (
    <Card className="flex min-w-0 flex-col gap-3 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-card text-foreground">Spans</h2>
        <span className="text-sm text-muted-foreground tabular">
          {formatInteger(spans.length)}
          <span className="sr-only"> spans</span>
        </span>
        {failed > 0 ? <Badge variant="danger">{formatInteger(failed)} failed</Badge> : null}
        {parents.length > 0 ? (
          <div className="ml-auto flex items-center">
            <Button
              variant="ghost"
              onClick={() => {
                setCollapsed(new Set());
              }}
            >
              Expand all
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setCollapsed(new Set(parents));
                const firstRoot = roots[0];
                if (firstRoot) {
                  onSelect(firstRoot.span.span_id);
                }
              }}
            >
              Collapse all
            </Button>
          </div>
        ) : null}
      </div>

      <div className="-mx-1 overflow-x-auto px-1">
        <div className={cn("flex flex-col gap-3", MIN_WATERFALL_WIDTH)}>
          <TimelineAxis ticks={ticks} />
          <div
            role="tree"
            aria-label="Spans"
            onKeyDown={handleKeyDown}
            className="flex flex-col gap-0.5"
          >
            {rows.map((row) => (
              <WaterfallRow
                key={row.span.span_id}
                row={row}
                timeline={timeline}
                ticks={ticks}
                selected={row.span.span_id === selectedSpanId}
                focusable={row.span.span_id === focusableId}
                onSelect={() => {
                  onSelect(row.span.span_id);
                }}
                onToggle={() => {
                  setExpanded(row.span.span_id, !row.expanded);
                }}
              />
            ))}
          </div>
        </div>
      </div>

      <WaterfallLegend spans={spans} />
    </Card>
  );
}

function TimelineAxis({ ticks }: { ticks: Tick[] }) {
  return (
    <div aria-hidden className={cn(ROW_GRID, "h-7 rounded-input bg-surface-muted pl-2")}>
      <span className="truncate text-overline text-muted-foreground uppercase">Name</span>
      <div className="relative h-full text-xs font-medium text-subtle-foreground">
        {ticks.map((tick, index) => (
          <span
            key={tick.ms}
            style={{ left: `${tick.percent}%` }}
            className={cn(
              "absolute top-1/2 -translate-y-1/2 whitespace-nowrap tabular",
              index > 0 && tick.percent < 99.5 && "-translate-x-1/2",
              index > 0 && tick.percent >= 99.5 && "-translate-x-full",
            )}
          >
            {formatTickLabel(tick.ms)}
          </span>
        ))}
      </div>
      <span />
    </div>
  );
}

interface WaterfallRowProps {
  row: SpanRow;
  timeline: Timeline;
  ticks: Tick[];
  selected: boolean;
  focusable: boolean;
  onSelect: () => void;
  onToggle: () => void;
}

function WaterfallRow({
  row,
  timeline,
  ticks,
  selected,
  focusable,
  onSelect,
  onToggle,
}: WaterfallRowProps) {
  const { span } = row;
  const layout = barLayout(span, timeline);
  const isError = span.status === "error";
  const duration = formatDuration(span.duration_ms) ?? "—";
  const startOffset = formatDuration(Date.parse(span.started_at) - timeline.startMs) ?? "0 ms";
  const ttftPercent = span.kind === "llm" ? ttftOffsetPercent(span) : null;
  const ttft = ttftPercent !== null ? formatDuration(span.time_to_first_token_ms) : null;

  return (
    <div
      id={spanRowId(span.span_id)}
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={selected}
      aria-expanded={row.hasChildren ? row.expanded : undefined}
      tabIndex={focusable ? 0 : -1}
      onClick={onSelect}
      className={cn(
        ROW_GRID,
        "h-10 cursor-pointer rounded-input text-sm outline-offset-[-2px] transition-colors select-none",
        selected ? "bg-surface-selected" : "hover:bg-surface-hover",
      )}
    >
      <div
        className="flex h-full min-w-0 items-center gap-1.5 pr-1"
        style={{ paddingLeft: `${BASE_INDENT_PX + INDENT_PER_LEVEL_PX * row.depth}px` }}
      >
        <span aria-hidden className="relative flex size-3 shrink-0 items-center justify-center">
          {row.hasChildren ? (
            <span
              onClick={(event) => {
                event.stopPropagation();
                onToggle();
              }}
              className="absolute -inset-1.5 flex items-center justify-center rounded-sm text-muted-foreground hover:bg-surface-hover hover:text-foreground"
            >
              <ChevronDown
                className={cn("size-3 transition-transform", !row.expanded && "-rotate-90")}
              />
            </span>
          ) : null}
        </span>
        <SpanKindIcon kind={span.kind} />
        <span
          title={span.name}
          className={cn(
            "min-w-0 flex-1 truncate",
            isError ? "text-danger-text" : "text-foreground",
            selected && "font-semibold",
          )}
        >
          {span.name}
        </span>
        {isError ? <CircleAlert aria-hidden className="size-3.5 shrink-0 text-danger" /> : null}
        <span className="sr-only">
          , {SPAN_KIND_META[span.kind].label} span{isError ? ", failed" : ""}
          {ttft ? `, first token after ${ttft}` : ""},
        </span>
      </div>

      <div className="relative h-full" aria-hidden>
        {ticks.map((tick) => (
          <span
            key={tick.ms}
            style={{ left: `${tick.percent}%` }}
            className="absolute inset-y-0 w-px bg-chart-grid"
          />
        ))}
        <span
          title={`${span.name}: ${duration}, starts at +${startOffset}${ttft ? `, first token after ${ttft}` : ""}`}
          style={{ left: `${layout.offsetPercent}%`, width: `${layout.widthPercent}%` }}
          className={cn(
            "absolute top-1/2 h-2.5 min-w-1 -translate-y-1/2 rounded-full",
            isError ? cn("bg-danger", ERROR_BAR_GLOW) : SPAN_KIND_META[span.kind].barClass,
          )}
        >
          {ttftPercent !== null ? (
            <span
              style={{ left: `${ttftPercent}%` }}
              className="absolute top-1/2 h-4.5 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-[2px] border border-rail bg-lime"
            />
          ) : null}
        </span>
      </div>

      <span className="truncate text-right text-xs font-medium text-muted-foreground tabular">
        {duration}
      </span>
    </div>
  );
}

/** Kind colours present in this trace, plus the TTFT marker when any span reports it. */
function WaterfallLegend({ spans }: { spans: Span[] }) {
  const kinds = kindsInTrace(spans);
  const showsTtft = spans.some((span) => span.kind === "llm" && ttftOffsetPercent(span) !== null);
  return (
    <ul
      aria-label="Legend"
      className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border pt-2 text-xs font-medium text-muted-foreground"
    >
      {kinds.map((kind) => (
        <li key={kind} className="flex items-center gap-1.5">
          <SpanKindIcon kind={kind} />
          {SPAN_KIND_META[kind].label}
        </li>
      ))}
      {showsTtft ? (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-3.5 w-[3px] rounded-[2px] border border-rail bg-lime" />
          Time to first token
        </li>
      ) : null}
    </ul>
  );
}

export function SpanWaterfallSkeleton() {
  return (
    <Card aria-hidden className="flex flex-col gap-3 p-5">
      <div className="flex h-10 items-center">
        <Skeleton className="h-5 w-24" />
      </div>
      <div className="h-7 rounded-input bg-surface-muted" />
      <div className="flex flex-col gap-0.5">
        {[0, 1, 2, 1, 1, 1].map((depth, index) => (
          <div key={index} className={cn(ROW_GRID, "h-10")}>
            <Skeleton
              className="h-3.5"
              style={{
                marginLeft: `${BASE_INDENT_PX + INDENT_PER_LEVEL_PX * depth}px`,
                width: "60%",
              }}
            />
            <div className="relative h-2.5">
              <Skeleton
                className="absolute inset-y-0 rounded-full"
                style={{ left: `${(index * 13) % 55}%`, width: `${25 + ((index * 17) % 40)}%` }}
              />
            </div>
            <Skeleton className="ml-auto h-3 w-10" />
          </div>
        ))}
      </div>
    </Card>
  );
}
