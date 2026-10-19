import Anthropic, { BadRequestError } from "@anthropic-ai/sdk";
import { describe, expect, it } from "vitest";

import { flush, wrapAnthropic } from "../src/index.js";
import {
  captureSpanlight,
  drain,
  type Json,
  jsonResponse,
  mockProvider,
  type SseEvent,
  sseResponse,
} from "./support.js";

const MESSAGE = {
  id: "msg_01XFDUDYJgAACzvnptvVoYEL",
  type: "message",
  role: "assistant",
  model: "claude-haiku-4-5-20251001",
  content: [{ type: "text", text: "Refunds take 5-7 business days." }],
  stop_reason: "end_turn",
  stop_sequence: null,
  usage: {
    input_tokens: 19,
    output_tokens: 12,
    cache_read_input_tokens: 1500,
    cache_creation_input_tokens: 6404,
    // Real usage from a 2026-10-14 pilot call: every write landed in the 1-hour cache.
    cache_creation: { ephemeral_5m_input_tokens: 0, ephemeral_1h_input_tokens: 6404 },
    service_tier: "priority_on_demand",
  },
};

function textStream(): SseEvent[] {
  const start = {
    type: "message_start",
    message: {
      id: "msg_stream_01",
      type: "message",
      role: "assistant",
      model: "claude-haiku-4-5-20251001",
      content: [],
      stop_reason: null,
      stop_sequence: null,
      usage: {
        input_tokens: 25,
        cache_creation_input_tokens: 0,
        cache_read_input_tokens: 0,
        output_tokens: 1,
        service_tier: "batch",
      },
    },
  };
  const delta = (text: string) => ({
    type: "content_block_delta",
    index: 0,
    delta: { type: "text_delta", text },
  });
  return [
    ["message_start", start],
    [
      "content_block_start",
      { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } },
    ],
    ["ping", { type: "ping" }],
    ["content_block_delta", delta("Hi")],
    ["content_block_delta", delta(", how can I help?")],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    [
      "message_delta",
      {
        type: "message_delta",
        delta: { stop_reason: "end_turn", stop_sequence: null },
        usage: { output_tokens: 15, server_tool_use: { web_search_requests: 3 } },
      },
    ],
    ["message_stop", { type: "message_stop" }],
  ];
}

function client(fetchMock: typeof fetch): Anthropic {
  return wrapAnthropic(new Anthropic({ apiKey: "sk-ant-test", fetch: fetchMock, maxRetries: 0 }));
}

const REQUEST = {
  model: "claude-haiku-4-5",
  max_tokens: 512,
  messages: [{ role: "user" as const, content: "Summarize our refund policy." }],
};

describe("wrapAnthropic", () => {
  it("counts cache reads and writes into input_tokens and keeps the 1-hour split", async () => {
    const capture = captureSpanlight();
    const anthropic = client(mockProvider(() => jsonResponse(MESSAGE)).fetch);

    const message = await anthropic.messages.create(REQUEST);
    await flush();

    expect(message.id).toBe(MESSAGE.id);
    const [span] = capture.spans();
    expect(span).toMatchObject({
      name: "chat claude-haiku-4-5",
      kind: "llm",
      provider: "anthropic",
      model: "claude-haiku-4-5-20251001",
      finish_reason: "end_turn",
      usage: {
        input_tokens: 19 + 1500 + 6404,
        output_tokens: 12,
        cached_tokens: 1500,
        cache_write_tokens: 6404,
        cache_write_1h_tokens: 6404,
        service_tier: "priority",
      },
      input: REQUEST,
      output: { role: "assistant", content: MESSAGE.content },
    });
    expect(span?.attributes).toMatchObject({ "response.stop_reason": "end_turn" });
  });

  it("streams with stream: true, cumulative usage and time to first token", async () => {
    const capture = captureSpanlight();
    const anthropic = client(mockProvider(() => sseResponse(textStream())).fetch);

    await drain(await anthropic.messages.create({ ...REQUEST, stream: true }));
    await flush();

    const [span] = capture.spans();
    expect(span).toMatchObject({
      usage: {
        input_tokens: 25,
        output_tokens: 15,
        service_tier: "batch",
        server_tool_use: { web_search_requests: 3 },
      },
      output: { role: "assistant", content: [{ type: "text", text: "Hi, how can I help?" }] },
      finish_reason: "end_turn",
    });
    expect(span?.time_to_first_token_ms).toBeGreaterThanOrEqual(0);
  });

  it("traces the messages.stream() helper once, through create", async () => {
    const capture = captureSpanlight();
    const anthropic = client(mockProvider(() => sseResponse(textStream())).fetch);

    const final = await anthropic.messages.stream(REQUEST).finalMessage();
    await flush();

    expect(final.content).toEqual([{ type: "text", text: "Hi, how can I help?" }]);
    expect(capture.spans()).toHaveLength(1);
    expect(capture.spans()[0]).toMatchObject({ usage: { input_tokens: 25, output_tokens: 15 } });
  });

  it("leaves the output count unknown when the stream ends before its final usage", async () => {
    const capture = captureSpanlight();
    const anthropic = client(mockProvider(() => sseResponse(textStream().slice(0, 4))).fetch);

    await drain(await anthropic.messages.create({ ...REQUEST, stream: true }));
    await flush();

    const [span] = capture.spans();
    expect(span?.usage).toEqual({
      input_tokens: 25,
      cached_tokens: 0,
      cache_write_tokens: 0,
      service_tier: "batch",
    });
    expect(span?.attributes).toMatchObject({ "usage.unreported_reason": "no_final_usage" });
  });

  it("re-throws the provider error unchanged and records it on the span", async () => {
    const capture = captureSpanlight();
    const body: Json = {
      type: "error",
      error: { type: "invalid_request_error", message: "max_tokens: Field required" },
      request_id: "req_011CSHoEeqs5C35K2UUqR7Fy",
    };
    const anthropic = client(mockProvider(() => jsonResponse(body, 400)).fetch);

    const error = await anthropic.messages.create(REQUEST).catch((caught: unknown) => caught);
    await flush();

    expect(error).toBeInstanceOf(BadRequestError);
    const [span] = capture.spans();
    expect(span).toMatchObject({ status: "error" });
    expect(span?.status_message).toContain("max_tokens: Field required");
    expect(span?.attributes).toMatchObject({
      "error.type": "BadRequestError",
      "http.status_code": 400,
    });
  });
});
