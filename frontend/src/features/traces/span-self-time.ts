/** Self time (spec D9): the part of a span's own duration no child covers. Pure functions. */
import { ownChildren } from "./span-retries";
import { timeOf } from "./span-timeline";
import type { SpanNode } from "./span-tree";

export interface Segment {
  /** Fractions of the span's duration, 0–1. */
  start: number;
  end: number;
}

/**
 * Pieces of the span's bar not covered by the union of its children's intervals (clipped to the
 * span). A span without children, or without a duration, is all self time. A retry group's
 * earlier attempts are not its children, so they never count.
 */
export function selfTimeSegments(node: SpanNode): Segment[] {
  const startMs = timeOf(node.span.started_at);
  const durationMs = node.span.duration_ms;
  if (durationMs <= 0) {
    return [{ start: 0, end: 1 }];
  }
  const clip = (ms: number) => Math.min(Math.max((ms - startMs) / durationMs, 0), 1);
  const covered = ownChildren(node)
    .map(({ span }) => {
      const childStart = timeOf(span.started_at);
      return { start: clip(childStart), end: clip(childStart + Math.max(span.duration_ms, 0)) };
    })
    .filter((segment) => segment.end > segment.start)
    .sort((a, b) => a.start - b.start);

  const self: Segment[] = [];
  let cursor = 0;
  for (const segment of covered) {
    if (segment.start > cursor) {
      self.push({ start: cursor, end: segment.start });
    }
    cursor = Math.max(cursor, segment.end);
  }
  if (cursor < 1) {
    self.push({ start: cursor, end: 1 });
  }
  return self;
}

/** Self time in ms, from the segments. */
export function selfTimeMs(node: SpanNode, segments: readonly Segment[]): number {
  const fraction = segments.reduce((sum, segment) => sum + segment.end - segment.start, 0);
  return Math.max(node.span.duration_ms, 0) * fraction;
}
