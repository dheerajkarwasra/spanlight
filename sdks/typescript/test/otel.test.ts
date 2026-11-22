import { describe, expect, it } from "vitest";

import { flush, init, withSpan } from "../src/index.js";
import {
  otlpTraceConfig,
  traceMcpServerTool,
  vercelAiTelemetry,
  withMcpToolCall,
} from "../src/otel.js";
import { captureSpanlight } from "./support.js";

describe("otel, Vercel AI, and MCP helpers", () => {
  it("builds experimental_telemetry config and OTLP endpoint settings", () => {
    init({ apiKey: "spl_live_test", host: "https://spanlight.test/" });
    const telemetry = vercelAiTelemetry({
      functionId: "support-chat",
      metadata: { org: "acme", "bad key!": "drop" },
    });
    expect(telemetry).toEqual({
      isEnabled: true,
      functionId: "support-chat",
      metadata: { org: "acme" },
      recordInputs: true,
      recordOutputs: true,
    });
    expect(otlpTraceConfig({ host: "https://spanlight.test/", apiKey: "spl_live_abc" })).toEqual({
      url: "https://spanlight.test/v1/otlp/traces",
      headers: { Authorization: "Bearer spl_live_abc" },
    });
  });

  it("propagates traceparent across MCP client and server tool spans", async () => {
    const capture = captureSpanlight();
    let capturedParams: Record<string, unknown> | undefined;
    withSpan("agent-loop", () => {
      return withMcpToolCall("search_docs", { query: "refunds" }, (params) => {
        capturedParams = params;
        return traceMcpServerTool("search_docs.server", params, () => "result");
      });
    });
    await flush();

    const spans = capture.spans();
    const clientTool = spans.find((s) => s.name === "search_docs");
    const serverTool = spans.find((s) => s.name === "search_docs.server");
    const agent = spans.find((s) => s.name === "agent-loop");
    expect(clientTool).toMatchObject({ kind: "tool", parent_span_id: agent?.span_id });
    expect(serverTool).toMatchObject({
      kind: "tool",
      trace_id: agent?.trace_id,
      parent_span_id: clientTool?.span_id,
    });
    expect((capturedParams?._meta as Record<string, unknown> | undefined)?.traceparent).toBeTypeOf(
      "string",
    );
  });
});
