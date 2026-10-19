import { describe, expect, it } from "vitest";

import { Exporter, type ExporterOptions, parseRetryAfter } from "../src/exporter.js";
import { captureSpanlight } from "./support.js";
import { flush, withSpan } from "../src/index.js";

type Reply = { status: number; body?: unknown; headers?: Record<string, string> };

/** An exporter whose `fetch` plays `replies` in order and records each request's span ids. */
function scripted(replies: Reply[], options: Partial<ExporterOptions> = {}) {
  const batches: string[][] = [];
  const delays: number[] = [];
  const exporter = new Exporter({
    url: "http://spanlight.test/v1/traces",
    apiKey: "spl_live_test",
    batchSize: 100,
    flushIntervalMs: 60_000,
    maxQueue: 10_000,
    gzip: false,
    timeoutMs: 1_000,
    maxRetries: 3,
    fetch: ((_url: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string) as { spans: { span_id: string }[] };
      batches.push(body.spans.map((span) => span.span_id));
      const reply = replies.shift() ?? { status: 202 };
      return Promise.resolve(
        new Response(JSON.stringify(reply.body ?? { accepted: 0, rejected: [] }), {
          status: reply.status,
          headers: reply.headers,
        }),
      );
    }) as typeof fetch,
    sleep: (ms: number) => {
      delays.push(ms);
      return Promise.resolve();
    },
    jitter: () => 0,
    ...options,
  });
  return { exporter, batches, delays };
}

const span = (id: string) => ({ span_id: id });

describe("Exporter", () => {
  it("sends gzip by default, with the bearer key and the client counters", async () => {
    const capture = captureSpanlight();
    withSpan("work", () => "done");
    await flush();

    const [request] = capture.requests;
    expect(request?.headers.get("content-encoding")).toBe("gzip");
    expect(request?.headers.get("authorization")).toBe("Bearer spl_live_test");
    expect(request?.headers.get("user-agent")).toMatch(/^spanlight-js\/\d/);
    expect(request?.body.client).toMatchObject({
      sdk: "spanlight-js",
      dropped_total: 0,
      failed_total: 0,
    });
    expect((request?.body.client as { exporter_id: string }).exporter_id).toMatch(/^[0-9a-f]{32}$/);
  });

  it("retries 429 and 5xx, honouring Retry-After, then succeeds", async () => {
    const { exporter, batches, delays } = scripted([
      { status: 503 },
      { status: 429, headers: { "retry-after": "2" } },
      { status: 202 },
    ]);
    exporter.export(span("a"));

    expect(await exporter.flush()).toBe(true);
    expect(batches).toEqual([["a"], ["a"], ["a"]]);
    expect(delays).toEqual([250, 2000]);
    expect(exporter.sentCount).toBe(1);
  });

  it("drops a batch after the last retry and counts it as failed", async () => {
    const { exporter, batches } = scripted(Array(4).fill({ status: 500 }) as Reply[]);
    exporter.export(span("a"));
    await exporter.flush();

    expect(batches).toHaveLength(4);
    expect(exporter.failedCount).toBe(1);
  });

  it("does not retry other 4xx", async () => {
    const { exporter, batches } = scripted([{ status: 401, body: { code: "UNAUTHORIZED" } }]);
    exporter.export(span("a"));
    await exporter.flush();

    expect(batches).toHaveLength(1);
    expect(exporter.failedCount).toBe(1);
  });

  it("splits a batch the server finds too large", async () => {
    const { exporter, batches } = scripted([{ status: 413 }]);
    for (const id of ["a", "b", "c", "d"]) exporter.export(span(id));
    await exporter.flush();

    expect(batches).toEqual([
      ["a", "b", "c", "d"],
      ["a", "b"],
      ["c", "d"],
    ]);
    expect(exporter.sentCount).toBe(4);
  });

  it("counts spans the server rejected", async () => {
    const rejected = [{ index: 1, span_id: "b", reason: "end_time before start_time" }];
    const { exporter } = scripted([{ status: 202, body: { accepted: 1, rejected } }]);
    exporter.export(span("a"));
    exporter.export(span("b"));
    await exporter.flush();

    expect(exporter.sentCount).toBe(1);
    expect(exporter.failedCount).toBe(1);
  });

  it("drops the oldest span when the queue is full", async () => {
    const { exporter, batches } = scripted([], { maxQueue: 2 });
    for (const id of ["a", "b", "c"]) exporter.export(span(id));
    await exporter.flush();

    expect(batches).toEqual([["b", "c"]]);
    expect(exporter.droppedCount).toBe(1);
  });

  it("sends a full batch without waiting for the interval", async () => {
    const { exporter, batches } = scripted([], { batchSize: 2 });
    exporter.export(span("a"));
    exporter.export(span("b"));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(batches).toEqual([["a", "b"]]);
  });
});

describe("parseRetryAfter", () => {
  it("reads delta-seconds and HTTP dates, and ignores junk", () => {
    expect(parseRetryAfter("1.5")).toBe(1500);
    expect(parseRetryAfter(new Date(Date.now() + 5_000).toUTCString())).toBeGreaterThan(3_000);
    expect(parseRetryAfter("Infinity")).toBeUndefined();
    expect(parseRetryAfter("soon")).toBeUndefined();
    expect(parseRetryAfter(null)).toBeUndefined();
  });
});
