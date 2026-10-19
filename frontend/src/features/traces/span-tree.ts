/**
 * Span tree and waterfall layout math. Pure functions only — no React here.
 */
import type { Span, SpanKind } from "@/lib/api";

export interface SpanNode {
  span: Span;
  children: SpanNode[];
  /** True when the span names a parent that isn't in this trace (still arriving, or dropped). */
  orphaned: boolean;
}

function timeOf(iso: string): number {
  const value = new Date(iso).getTime();
  return Number.isNaN(value) ? 0 : value;
}

function compareSpans(a: Span, b: Span): number {
  const byStart = timeOf(a.started_at) - timeOf(b.started_at);
  if (byStart !== 0) {
    return byStart;
  }
  return a.span_id.localeCompare(b.span_id);
}

/**
 * Builds the span forest from `parent_span_id`. Spans whose parent is missing
 * become roots. Siblings are ordered by start time. Cycles (malformed data)
 * are broken so every span appears exactly once.
 */
export function buildSpanTree(spans: readonly Span[]): SpanNode[] {
  const byId = new Map<string, Span>();
  for (const span of spans) {
    if (!byId.has(span.span_id)) {
      byId.set(span.span_id, span);
    }
  }

  const childrenByParent = new Map<string, Span[]>();
  const rootSpans: Span[] = [];
  for (const span of byId.values()) {
    const parentId = span.parent_span_id;
    if (parentId !== null && parentId !== span.span_id && byId.has(parentId)) {
      const siblings = childrenByParent.get(parentId) ?? [];
      siblings.push(span);
      childrenByParent.set(parentId, siblings);
    } else {
      rootSpans.push(span);
    }
  }

  const placed = new Set<string>();

  function toNode(span: Span): SpanNode {
    placed.add(span.span_id);
    const children = (childrenByParent.get(span.span_id) ?? [])
      .filter((child) => !placed.has(child.span_id))
      .sort(compareSpans)
      .map(toNode);
    const orphaned = span.parent_span_id !== null && !byId.has(span.parent_span_id);
    return { span, children, orphaned };
  }

  const roots = rootSpans.sort(compareSpans).map(toNode);

  // Spans caught in a parent cycle are unreachable from any root: surface them as roots.
  const unplaced = [...byId.values()].filter((span) => !placed.has(span.span_id));
  for (const span of unplaced.sort(compareSpans)) {
    if (!placed.has(span.span_id)) {
      roots.push({ ...toNode(span), orphaned: true });
    }
  }

  return roots;
}

export interface SpanRow {
  span: Span;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
  orphaned: boolean;
}

/** Depth-first rows for the waterfall, skipping children of collapsed spans. */
export function flattenSpanTree(
  roots: readonly SpanNode[],
  collapsed: ReadonlySet<string>,
): SpanRow[] {
  const rows: SpanRow[] = [];

  function visit(node: SpanNode, depth: number): void {
    const expanded = !collapsed.has(node.span.span_id);
    rows.push({
      span: node.span,
      depth,
      hasChildren: node.children.length > 0,
      expanded,
      orphaned: node.orphaned,
    });
    if (!expanded) {
      return;
    }
    for (const child of node.children) {
      visit(child, depth + 1);
    }
  }

  for (const root of roots) {
    visit(root, 0);
  }
  return rows;
}

/** Every span id that has children — handy for "collapse all". */
export function parentSpanIds(roots: readonly SpanNode[]): string[] {
  const ids: string[] = [];
  const stack = [...roots];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node && node.children.length > 0) {
      ids.push(node.span.span_id);
      stack.push(...node.children);
    }
  }
  return ids;
}

/** The ids of a span's ancestors, nearest first. Used to reveal a selected span. */
export function ancestorIds(spans: readonly Span[], spanId: string): string[] {
  const byId = new Map(spans.map((span) => [span.span_id, span]));
  const ancestors: string[] = [];
  const seen = new Set<string>([spanId]);
  let parentId = byId.get(spanId)?.parent_span_id ?? null;
  while (parentId !== null && byId.has(parentId) && !seen.has(parentId)) {
    ancestors.push(parentId);
    seen.add(parentId);
    parentId = byId.get(parentId)?.parent_span_id ?? null;
  }
  return ancestors;
}

export interface Timeline {
  startMs: number;
  endMs: number;
  /** Always at least 1 ms so layout math never divides by zero. */
  durationMs: number;
}

export function computeTimeline(spans: readonly Span[]): Timeline {
  if (spans.length === 0) {
    return { startMs: 0, endMs: 1, durationMs: 1 };
  }
  let startMs = Number.POSITIVE_INFINITY;
  let endMs = Number.NEGATIVE_INFINITY;
  for (const span of spans) {
    const start = timeOf(span.started_at);
    const end = Math.max(timeOf(span.ended_at), start + Math.max(span.duration_ms, 0));
    startMs = Math.min(startMs, start);
    endMs = Math.max(endMs, end);
  }
  const durationMs = Math.max(endMs - startMs, 1);
  return { startMs, endMs: startMs + durationMs, durationMs };
}

export interface BarLayout {
  /** Left edge, percent of the timeline (0–100). */
  offsetPercent: number;
  /** Width, percent of the timeline. Never below `minWidthPercent`. */
  widthPercent: number;
}

export const MIN_BAR_WIDTH_PERCENT = 0.5;

/** Positions a span's bar on the timeline; tiny spans still get a visible sliver. */
export function barLayout(
  span: Span,
  timeline: Timeline,
  minWidthPercent: number = MIN_BAR_WIDTH_PERCENT,
): BarLayout {
  const start = timeOf(span.started_at) - timeline.startMs;
  const duration = Math.max(span.duration_ms, 0);
  const rawOffset = (start / timeline.durationMs) * 100;
  const rawWidth = (duration / timeline.durationMs) * 100;

  const widthPercent = Math.min(Math.max(rawWidth, minWidthPercent), 100);
  const offsetPercent = Math.min(Math.max(rawOffset, 0), 100 - widthPercent);
  return { offsetPercent, widthPercent };
}

/**
 * Picks a "nice" step (1, 2, 5 × 10ⁿ ms) giving at most `maxTicks` intervals.
 * Never below 1 ms: sub-millisecond ticks aren't useful labels.
 */
export function niceTickStep(durationMs: number, maxTicks = 5): number {
  if (!Number.isFinite(durationMs) || durationMs <= 0) {
    return 1;
  }
  const rough = durationMs / Math.max(maxTicks, 1);
  if (rough <= 1) {
    return 1;
  }
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  for (const multiplier of [1, 2, 5]) {
    const step = multiplier * magnitude;
    if (step >= rough) {
      return step;
    }
  }
  return 10 * magnitude;
}

export interface Tick {
  ms: number;
  percent: number;
}

/** Axis ticks from 0 to the trace duration, at nice round intervals. */
export function timelineTicks(durationMs: number, maxTicks = 5): Tick[] {
  const step = niceTickStep(durationMs, maxTicks);
  const count = Math.floor(durationMs / step + 1e-9);
  const ticks: Tick[] = [];
  for (let index = 0; index <= count; index += 1) {
    const ms = index * step;
    ticks.push({ ms, percent: (ms / durationMs) * 100 });
  }
  return ticks;
}

function trimNumber(value: number): string {
  return String(Number(value.toFixed(2)));
}

/** Compact axis labels: "0", "500ms", "1.5s", "2m 30s". Ticks are round numbers, so this stays short. */
export function formatTickLabel(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) {
    return "0";
  }
  if (ms < 1000) {
    return `${trimNumber(ms)}ms`;
  }
  if (ms < 60_000) {
    return `${trimNumber(ms / 1000)}s`;
  }
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

/**
 * Where the time-to-first-token marker sits inside a span's bar, as a percent of the bar
 * (0–100). Null when the SDK didn't report TTFT or the span has no duration.
 */
export function ttftOffsetPercent(span: Span): number | null {
  const ttft = span.time_to_first_token_ms;
  if (ttft === null || !Number.isFinite(ttft) || span.duration_ms <= 0) {
    return null;
  }
  return Math.min(Math.max((ttft / span.duration_ms) * 100, 0), 100);
}

/** Legend order for span kinds. */
export const SPAN_KIND_ORDER: readonly SpanKind[] = [
  "llm",
  "tool",
  "retrieval",
  "chain",
  "agent",
  "http",
  "other",
];

/** The span kinds that occur in a trace, in legend order. */
export function kindsInTrace(spans: readonly Span[]): SpanKind[] {
  const present = new Set(spans.map((span) => span.kind));
  return SPAN_KIND_ORDER.filter((kind) => present.has(kind));
}

export function failedSpanCount(spans: readonly Span[]): number {
  return spans.filter((span) => span.status === "error").length;
}
