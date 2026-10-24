/**
 * Waterfall timeline math: the trace's time window, bar positions and axis ticks. Pure functions.
 */
import type { Span } from "@/lib/api";

/** Epoch ms of an ISO timestamp; 0 when it doesn't parse. */
export function timeOf(iso: string): number {
  const value = new Date(iso).getTime();
  return Number.isNaN(value) ? 0 : value;
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
