import { ChevronDown, CircleAlert, TriangleAlert } from "lucide-react";
import type { CSSProperties } from "react";

import { Badge } from "@/components/ui/badge";
import type { Insight } from "@/lib/api";
import { formatDuration, formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { SpanKindIcon } from "./span-kind";
import { kindMeta } from "./span-kind-meta";
import { ownChildren, retryCounts } from "./span-retries";
import { selfTimeMs, selfTimeSegments } from "./span-self-time";
import { barLayout, ttftOffsetPercent, type Tick, type Timeline } from "./span-timeline";
import type { SpanNode, SpanRow, TreeItemMeta } from "./span-tree";
import { indentPx, INLINE_BAR, ROW_GRID, spanRowId, TIMELINE_CELL } from "./waterfall-items";
import { BarFill } from "./waterfall-parts";

/** Error bars glow softly in the danger colour. */
const ERROR_BAR_GLOW = "shadow-[0_0_5px_color-mix(in_srgb,var(--danger)_55%,transparent)]";

interface WaterfallRowProps {
  row: SpanRow;
  /** The span's node with all its children (not cut by the kind filter), for self time. */
  node: SpanNode | undefined;
  meta: TreeItemMeta | undefined;
  style: CSSProperties | undefined;
  timeline: Timeline;
  ticks: Tick[];
  selected: boolean;
  focusable: boolean;
  /** Not a search or kind-filter match: shown as context. */
  dimmed: boolean;
  /** The Doctor finding to flag on this row (spec D10). */
  doctor: Insight | undefined;
  onSelect: () => void;
  onToggle: () => void;
}

export function WaterfallRow(props: WaterfallRowProps) {
  const { row, node, meta, style, timeline, selected, focusable, dimmed, doctor } = props;
  const { span } = row;
  const layout = barLayout(span, timeline);
  const isError = span.status === "error";
  const duration = formatDuration(span.duration_ms) ?? "—";
  const startOffset = formatDuration(Date.parse(span.started_at) - timeline.startMs) ?? "0 ms";
  const ttftPercent = span.kind === "llm" ? ttftOffsetPercent(span) : null;
  const ttft = ttftPercent !== null ? formatDuration(span.time_to_first_token_ms) : null;
  const segments = node ? selfTimeSegments(node) : [{ start: 0, end: 1 }];
  const selfMs = node ? selfTimeMs(node, segments) : span.duration_ms;
  const self = node && ownChildren(node).length > 0 ? formatDuration(selfMs) : null;
  const children = self ? formatDuration(Math.max(span.duration_ms - selfMs, 0)) : null;
  const retries = node ? retryCounts(node) : null;
  const fill = isError ? "bg-danger" : kindMeta(span.kind).barClass;
  const barStyle = { left: `${layout.offsetPercent}%`, width: `${layout.widthPercent}%` };

  return (
    <div
      id={spanRowId(span.span_id)}
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={selected}
      aria-expanded={row.hasChildren ? row.expanded : undefined}
      aria-setsize={meta?.setSize}
      aria-posinset={meta?.posInSet}
      tabIndex={focusable ? 0 : -1}
      style={style}
      onClick={props.onSelect}
      className={cn(
        ROW_GRID,
        "h-10 cursor-pointer rounded-input text-sm outline-offset-[-2px] transition-colors select-none",
        selected ? "bg-surface-selected" : "hover:bg-surface-hover",
      )}
    >
      <div
        className="relative flex h-full min-w-0 items-center gap-1.5 pr-1"
        style={{ paddingLeft: `${indentPx(row.depth)}px` }}
      >
        <span aria-hidden className="relative flex size-3 shrink-0 items-center justify-center">
          {row.hasChildren ? (
            <span
              onClick={(event) => {
                event.stopPropagation();
                props.onToggle();
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
            dimmed ? "text-muted-foreground" : isError ? "text-danger-text" : "text-foreground",
            selected && "font-semibold",
          )}
        >
          {span.name}
        </span>
        {retries ? (
          <Badge size="sm" variant={retries.failed > 0 ? "danger" : "neutral"} className="shrink-0">
            {formatInteger(retries.attempts)} attempts
            {retries.failed > 0 ? ` · ${formatInteger(retries.failed)} failed` : ""}
          </Badge>
        ) : null}
        {row.hasChildren && !row.expanded && meta && !retries ? (
          <Badge size="sm">+{formatInteger(meta.descendants)} spans</Badge>
        ) : null}
        {doctor ? (
          <TriangleAlert
            role="img"
            aria-label={`Doctor: ${doctor.title}`}
            className={cn(
              "size-3.5 shrink-0",
              doctor.severity === "critical" ? "text-danger" : "text-warning",
            )}
          />
        ) : null}
        {isError ? <CircleAlert aria-hidden className="size-3.5 shrink-0 text-danger" /> : null}
        <span className="sr-only">
          , {kindMeta(span.kind).label} span{isError ? ", failed" : ""}
          {ttft ? `, first token after ${ttft}` : ""}
          {self ? `, self ${self}` : ""},
        </span>
        <span
          aria-hidden
          style={{ left: `${indentPx(row.depth)}px` }}
          className={cn(INLINE_BAR, "absolute right-1 bottom-1 h-1", dimmed && "opacity-40")}
        >
          <span
            style={barStyle}
            className={cn("absolute inset-y-0 min-w-1 rounded-full", isError && ERROR_BAR_GLOW)}
          >
            <BarFill fill={fill} segments={segments} />
          </span>
        </span>
      </div>

      <div className={cn(TIMELINE_CELL, "relative h-full", dimmed && "opacity-40")} aria-hidden>
        {props.ticks.map((tick) => (
          <span
            key={tick.ms}
            style={{ left: `${tick.percent}%` }}
            className="absolute inset-y-0 w-px bg-chart-grid"
          />
        ))}
        <span
          title={`${span.name}: ${duration}, starts at +${startOffset}${ttft ? `, first token after ${ttft}` : ""}${self && children ? ` · Self ${self} · children ${children}` : ""}`}
          style={barStyle}
          className={cn(
            "absolute top-1/2 h-2.5 min-w-1 -translate-y-1/2 rounded-full",
            isError && ERROR_BAR_GLOW,
          )}
        >
          <BarFill fill={fill} segments={segments} />
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
