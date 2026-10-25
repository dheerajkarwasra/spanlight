/**
 * W3C Trace Context propagation across processes: MCP `_meta`, HTTP headers, queues.
 *
 * `inject` writes the current span as a `traceparent` into a carrier object, and `continueTrace`
 * makes the span a `traceparent` names the parent of the spans opened inside `fn`. `tracestate`
 * and `baggage` are left as they are. The W3C sampled flag is not honoured: the SDK records
 * every span.
 *
 * Only continue traces from callers you trust: a caller chooses the trace id, so it can attach
 * your spans to another trace of the same project.
 */

import { context } from "./client.js";
import { Span, TraceState } from "./span.js";

const TRACEPARENT = /^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}/;

/**
 * A copy of `carrier` with the current span as its `traceparent`. A `traceparent` already in the
 * carrier is kept; outside a recording span the copy is returned unchanged.
 */
export function inject(carrier?: Record<string, unknown> | null): Record<string, unknown> {
  const result: Record<string, unknown> = { ...carrier };
  const span = context.getStore();
  if (span?.recording && !("traceparent" in result))
    result.traceparent = `00-${span.traceId}-${span.spanId}-01`;
  return result;
}

/** `[traceId, parentSpanId]` of a valid W3C `traceparent`, else undefined. */
function parse(value: unknown): [string, string] | undefined {
  if (typeof value !== "string") return undefined;
  const match = TRACEPARENT.exec(value);
  if (match === null) return undefined;
  const [, version, traceId = "", spanId = ""] = match;
  // Version 00 is exactly 55 characters; a later version may add fields after a dash.
  const exact = value.length === 55;
  if (version === "ff" || !(exact || (version !== "00" && value[55] === "-"))) return undefined;
  if (/^0+$/.test(traceId) || /^0+$/.test(spanId)) return undefined;
  return [traceId, spanId];
}

/**
 * Run `fn` as a continuation of the trace named by `carrier.traceparent`: spans opened inside
 * become children of the remote span. A missing or invalid `traceparent` is ignored, so the next
 * span starts a new trace.
 *
 * The remote parent is a placeholder that is never sent; `currentSpan()` returns it inside `fn`,
 * and setters called on it are lost.
 */
export function continueTrace<T>(
  carrier: Record<string, unknown> | null | undefined,
  fn: () => T,
): T {
  const ids = parse(carrier?.traceparent);
  if (ids === undefined) return fn();
  // No name, environment or release: the caller's trace fields must win on the server.
  const trace = Object.assign(new TraceState(undefined, undefined, undefined), { traceId: ids[0] });
  // Recording, or `startSpan` ignores it as a parent; no `onEnd`, so it is never exported.
  const placeholder = Object.assign(
    new Span("remote parent", "other", trace, undefined, undefined),
    {
      spanId: ids[1],
    },
  );
  return context.run(placeholder, fn);
}
