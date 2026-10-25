import { describe, expect, it } from "vitest";

import { continueTrace, flush, inject, withSpan } from "../src/index.js";
import { captureSpanlight } from "./support.js";

describe("inject and continueTrace", () => {
  it("parents spans on the injected span and restarts on an invalid traceparent", async () => {
    const capture = captureSpanlight();
    const { meta, traceId, spanId } = withSpan("caller", (span) => ({
      meta: inject({ progressToken: 1 }),
      traceId: span.traceId,
      spanId: span.spanId,
    }));
    expect(meta).toEqual({ progressToken: 1, traceparent: `00-${traceId}-${spanId}-01` });

    // Outside the caller's span, as a server process would be.
    continueTrace(meta, () => withSpan("handle-tool", () => undefined));
    continueTrace({ traceparent: `00-${"0".repeat(32)}-${spanId}-01` }, () =>
      withSpan("handle-tool", () => undefined),
    );
    await flush();

    const spans = capture.spans();
    expect(spans).toHaveLength(3); // the placeholder parent is never exported
    const [child, restarted] = spans.filter((span) => span.name === "handle-tool");
    expect(child).toMatchObject({ trace_id: traceId, parent_span_id: spanId });
    expect(child?.trace).toMatchObject({ name: null, environment: null });
    // An all-zero trace id is invalid: the span starts a new trace of its own.
    expect(restarted?.trace_id).not.toBe(traceId);
    expect(restarted?.parent_span_id).toBeNull();
  });
});
