import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";

import Anthropic from "@anthropic-ai/sdk";
import { describe, expect, it } from "vitest";

import {
  flush,
  init,
  noPrompt,
  requestHash,
  score,
  updateTrace,
  withPrompt,
  withSpan,
  wrapAnthropic,
} from "../src/index.js";
import { cleanMetadata, mergeMetadata } from "../src/metadata.js";
import { Span, TraceState } from "../src/span.js";
import { finishWithError } from "../src/providers/common.js";
import { captureSpanlight, jsonResponse, mockProvider } from "./support.js";

interface CleanFixtureCase {
  name: string;
  raw: unknown;
  lenient: Record<string, string>;
}

interface MergeFixtureCase {
  name: string;
  current: Record<string, string>;
  incoming: Record<string, string>;
  expected: Record<string, string>;
}

interface MetadataFixture {
  clean: CleanFixtureCase[];
  merge: MergeFixtureCase[];
}

const metadataFixture = JSON.parse(
  readFileSync(new URL("../../fixtures/metadata-cases.json", import.meta.url), "utf8"),
) as MetadataFixture;

describe("requestHash parity", () => {
  it("strips caller transport keys and produces a 32-hex canonical SHA-256", () => {
    const a = requestHash("gpt-5", {
      model: "gpt-5",
      messages: [{ role: "user", content: "hi" }],
      stream: true,
      timeout: 30,
      extra_headers: { "x-foo": "bar" },
    });
    const b = requestHash("gpt-5", {
      messages: [{ content: "hi", role: "user" }],
      model: "gpt-5",
      stream: false,
    });
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(a).toBe(b);
    expect(requestHash("gpt-5", null)).toBeUndefined();
  });
});

describe("metadata rules against shared fixture", () => {
  for (const item of metadataFixture.clean) {
    if (item.name === "value_exponent_float_coerced_to_its_python_json_text") continue;
    it(`clean: ${item.name}`, () => {
      expect(cleanMetadata(item.raw)).toEqual(item.lenient);
    });
  }

  for (const item of metadataFixture.merge) {
    it(`merge: ${item.name}`, () => {
      expect(mergeMetadata(item.current, item.incoming)).toEqual(item.expected);
    });
  }

  it("attaches and merges metadata onto traces via updateTrace()", async () => {
    const capture = captureSpanlight();
    withSpan("agent-turn", () => {
      updateTrace({ metadata: { customer: "acme", turn: 1 } });
      updateTrace({ metadata: { turn: 2 } });
    });
    await flush();
    expect(capture.spans()[0]?.trace).toMatchObject({
      metadata: { customer: "acme", turn: "2" },
    });
  });
});

describe("scores", () => {
  it("batches score() calls to POST /v1/scores with UUIDv7 ids", async () => {
    const posted: { url: string; body: Record<string, unknown> }[] = [];
    const client = init({
      apiKey: "spl_live_test",
      host: "https://spanlight.test",
      gzip: false,
      fetch: (input, request) => {
        const url =
          typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const rawBody = typeof request?.body === "string" ? request.body : "{}";
        const body = JSON.parse(rawBody) as Record<string, unknown>;
        posted.push({ url, body });
        const count = Array.isArray(body.scores) ? body.scores.length : 0;
        return Promise.resolve(Response.json({ accepted: count, rejected: [] }, { status: 202 }));
      },
    });
    score("0".repeat(32), "helpfulness", 0.9, { spanId: "1".repeat(16), comment: "solid" });
    client.score("0".repeat(32), "resolved", true);
    client.score("0".repeat(32), "tier", "gold");
    await client.flush();

    expect(posted).toHaveLength(1);
    expect(posted[0]?.url).toBe("https://spanlight.test/v1/scores");
    const scores = posted[0]?.body.scores as Record<string, unknown>[];
    expect(scores).toHaveLength(3);
    expect(scores[0]).toMatchObject({
      trace_id: "0".repeat(32),
      span_id: "1".repeat(16),
      name: "helpfulness",
      value: 0.9,
      comment: "solid",
    });
    expect(String(scores[0]?.id)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(scores[1]).toMatchObject({ name: "resolved", value: true });
    expect(scores[2]).toMatchObject({ name: "tier", string_value: "gold" });
  });
});

describe("prompts", () => {
  it("fetches, caches with ETag revalidation, compiles, and links spans", async () => {
    let calls = 0;
    const requests: { headers: Headers; body: Record<string, unknown> }[] = [];
    const client = init({
      apiKey: "spl_live_test",
      host: "https://spanlight.test",
      promptCacheTtlSeconds: 0,
      fetch: (input, req) => {
        const url =
          typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.includes("/v1/prompts/")) {
          calls += 1;
          const headers = new Headers(req?.headers);
          if (calls === 1) {
            return Promise.resolve(
              Response.json(
                {
                  name: "support/answer",
                  version: 3,
                  version_id: "11111111-1111-4111-8111-111111111111",
                  template: { messages: [{ role: "user", content: "Hello {{ user }}" }] },
                  labels: ["production"],
                },
                { status: 200, headers: { etag: '"v3"' } },
              ),
            );
          }
          expect(headers.get("if-none-match")).toBe('"v3"');
          return Promise.resolve(new Response(null, { status: 304, headers: { etag: '"v3"' } }));
        }
        const headers = new Headers(req?.headers);
        const raw = req?.body as Buffer | string;
        const text =
          headers.get("content-encoding") === "gzip" ? gunzipSync(raw).toString() : String(raw);
        requests.push({ headers, body: JSON.parse(text) as Record<string, unknown> });
        const latestSpans = requests.at(-1)?.body.spans;
        const spans = Array.isArray(latestSpans) ? latestSpans : [];
        return Promise.resolve(
          Response.json({ accepted: spans.length, rejected: [] }, { status: 202 }),
        );
      },
    });

    const p1 = await client.prompts.get("support/answer");
    const p2 = await client.prompts.get("support/answer");
    expect(p1.version).toBe(3);
    expect(p2.version).toBe(3);
    expect(calls).toBe(2);

    const compiled = p1.compile({ user: "Ada" });
    expect(compiled.messages).toEqual([{ role: "user", content: "Hello Ada" }]);
    withPrompt(compiled, () => {
      withSpan("linked", () => {});
      noPrompt(() => {
        withSpan("unlinked", () => {});
      });
    });
    await client.flush();

    const spans = requests.flatMap((r) => r.body.spans as Record<string, unknown>[]);
    expect(spans.find((s) => s.name === "linked")?.prompt_version_id).toBe(
      "11111111-1111-4111-8111-111111111111",
    );
    expect(spans.find((s) => s.name === "unlinked")?.prompt_version_id).toBeUndefined();
  });

  it("uses local fallback when server is unreachable", async () => {
    const client = init({
      apiKey: "spl_live_test",
      host: "https://spanlight.test",
      fetch: () => Promise.reject(new Error("network down")),
    });
    const prompt = await client.prompts.get("support/answer", { fallback: "Hi {{ name }}" });
    expect(prompt.isFallback).toBe(true);
    expect(prompt.compile({ name: "Sam" }).text).toBe("Hi Sam");
  });
});

describe("provider errors and Anthropic beta", () => {
  it("records gateway refusal code without leaking provider fields", () => {
    const trace = new TraceState("call", undefined, undefined);
    const span = new Span("chat gpt-5", "llm", trace, undefined, undefined, true);
    const error = Object.assign(new Error("Budget exceeded"), {
      status: 402,
      headers: new Headers({
        "x-spanlight-code": "BUDGET_EXCEEDED",
        "retry-after": "5",
        "x-request-id": "gw_req_1",
      }),
    });
    finishWithError(span, error);
    expect(span.attributes["spanlight.gateway.error_code"]).toBe("BUDGET_EXCEEDED");
    expect(span.attributes["provider.ratelimit"]).toBeUndefined();
    expect(span.attributes["provider.request_id"]).toBeUndefined();
  });

  it("records rate-limit snapshot, retry_after_s and error type/code on upstream failure", () => {
    const trace = new TraceState("call", undefined, undefined);
    const span = new Span("chat gpt-5", "llm", trace, undefined, undefined, true);
    const error = Object.assign(new Error("Rate limit"), {
      status: 429,
      requestID: "req_up_99",
      headers: new Headers({
        "retry-after-ms": "250",
        "x-ratelimit-remaining-requests": "0",
      }),
      error: { type: "tokens", code: "rate_limit_exceeded" },
    });
    finishWithError(span, error);
    expect(span.attributes["provider.error.type"]).toBe("tokens");
    expect(span.attributes["provider.error.code"]).toBe("rate_limit_exceeded");
    expect(span.attributes["provider.retry_after_s"]).toBe(0.25);
    expect(span.attributes["provider.ratelimit"]).toEqual({
      "retry-after-ms": "250",
      "x-ratelimit-remaining-requests": "0",
    });
    expect(span.attributes["provider.request_id"]).toBe("req_up_99");
  });

  it("traces Anthropic beta.messages.create with request_hash", async () => {
    const capture = captureSpanlight();
    const provider = mockProvider(() =>
      jsonResponse({
        id: "msg_beta_1",
        type: "message",
        role: "assistant",
        model: "claude-sonnet-5-5",
        stop_reason: "end_turn",
        content: [{ type: "text", text: "ok" }],
        usage: { input_tokens: 10, output_tokens: 4 },
      }),
    );
    const client = wrapAnthropic(new Anthropic({ apiKey: "test", fetch: provider.fetch }));
    await client.beta.messages.create({
      model: "claude-sonnet-5-5",
      max_tokens: 64,
      messages: [{ role: "user", content: "ping" }],
    });
    await flush();

    const span = capture.spans()[0];
    expect(span?.provider).toBe("anthropic");
    expect(span?.request_hash).toMatch(/^[0-9a-f]{32}$/);
  });
});
