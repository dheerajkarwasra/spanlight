import OpenAI, { RateLimitError } from "openai";
import { describe, expect, it } from "vitest";

import { flush, observe, wrapOpenAI } from "../src/index.js";
import {
  captureSpanlight,
  drain,
  type Json,
  jsonResponse,
  mockProvider,
  type SseEvent,
  sseResponse,
} from "./support.js";

const CHAT_COMPLETION = {
  id: "chatcmpl-B9MBs8CjcvOU2jLn4n570S5qMJKcT",
  object: "chat.completion",
  created: 1741569952,
  model: "gpt-5.6-2026-09-30",
  choices: [
    {
      index: 0,
      message: {
        role: "assistant",
        content: "You can request a refund within 30 days.",
        refusal: null,
        annotations: [],
      },
      logprobs: null,
      finish_reason: "stop",
    },
  ],
  usage: {
    prompt_tokens: 15000,
    completion_tokens: 11,
    total_tokens: 15011,
    prompt_tokens_details: { cached_tokens: 12000, cache_write_tokens: 3000, audio_tokens: 0 },
    completion_tokens_details: { reasoning_tokens: 4, audio_tokens: 0 },
  },
  service_tier: "default",
};

function chunk(delta: Json | null, extra: Json = {}): Json {
  return {
    id: "chatcmpl-stream-1",
    object: "chat.completion.chunk",
    created: 1741569952,
    model: "gpt-4o-mini-2024-07-18",
    service_tier: "flex",
    choices:
      delta === null
        ? []
        : [{ index: 0, delta, logprobs: null, finish_reason: extra.finish_reason ?? null }],
    usage: extra.usage ?? null,
  };
}

function textStream(includeUsage: boolean): SseEvent[] {
  const events: SseEvent[] = [
    [null, chunk({ role: "assistant", content: "", refusal: null })],
    [null, chunk({ content: "Hello" })],
    [null, chunk({ content: " there" })],
    [null, chunk({}, { finish_reason: "stop" })],
  ];
  if (includeUsage) {
    const usage = {
      prompt_tokens: 20,
      completion_tokens: 3,
      total_tokens: 23,
      prompt_tokens_details: { cached_tokens: 0 },
    };
    events.push([null, chunk(null, { usage })]);
  }
  events.push([null, "[DONE]"]);
  return events;
}

function responseObject(status = "completed"): Json {
  return {
    id: "resp_67ccd2bed1ec8190b14f964abc0542670bb6a6b452e3a6",
    object: "response",
    created_at: 1741476542,
    status,
    error: null,
    model: "gpt-4.1-2025-04-14",
    output: [
      {
        type: "web_search_call",
        id: "ws_1",
        status: "completed",
        action: { type: "search", query: "order 42" },
      },
      {
        type: "web_search_call",
        id: "ws_2",
        status: "in_progress",
        action: { type: "search", query: "carrier" },
      },
      {
        type: "message",
        id: "msg_67ccd2bf17f0819081ff3bb2cf6508e6",
        status: "completed",
        role: "assistant",
        content: [{ type: "output_text", text: "Your order has shipped.", annotations: [] }],
      },
    ],
    usage: {
      input_tokens: 36,
      input_tokens_details: { cached_tokens: 8 },
      output_tokens: 6,
      output_tokens_details: { reasoning_tokens: 0 },
      total_tokens: 42,
    },
    service_tier: "priority",
  };
}

function client(fetchMock: typeof fetch): OpenAI {
  return wrapOpenAI(new OpenAI({ apiKey: "sk-test", fetch: fetchMock, maxRetries: 0 }));
}

describe("wrapOpenAI chat completions", () => {
  it("records model, usage with cache reads and writes, tier and output under the parent", async () => {
    const capture = captureSpanlight();
    const openai = client(mockProvider(() => jsonResponse(CHAT_COMPLETION)).fetch);
    const answer = observe(async (question: string) => {
      const { data, request_id } = await openai.chat.completions
        .create({ model: "gpt-5.6", messages: [{ role: "user", content: question }] })
        .withResponse();
      return { text: data.choices[0]?.message.content, request_id };
    });

    const result = await answer("How do refunds work?");
    await flush();

    expect(result).toEqual({
      text: "You can request a refund within 30 days.",
      request_id: "req_mock_123",
    });
    const [llm, root] = capture.spans();
    expect(root).toMatchObject({ name: "anonymous", kind: "chain", parent_span_id: null });
    expect(llm).toMatchObject({
      name: "chat gpt-5.6",
      kind: "llm",
      status: "ok",
      provider: "openai",
      model: "gpt-5.6-2026-09-30",
      parent_span_id: root?.span_id,
      trace_id: root?.trace_id,
      finish_reason: "stop",
      usage: {
        input_tokens: 15000,
        output_tokens: 11,
        cached_tokens: 12000,
        cache_write_tokens: 3000,
        service_tier: "standard",
      },
      input: { model: "gpt-5.6", messages: [{ role: "user", content: "How do refunds work?" }] },
      output: { role: "assistant", content: "You can request a refund within 30 days." },
    });
    expect(llm?.attributes).toMatchObject({
      "usage.reasoning_tokens": 4,
      "response.id": CHAT_COMPLETION.id,
    });
  });

  it("streams with usage: assembles the output and records time to first token", async () => {
    const capture = captureSpanlight();
    const openai = client(mockProvider(() => sseResponse(textStream(true))).fetch);

    const stream = await openai.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "hi" }],
      stream: true,
      stream_options: { include_usage: true },
    });
    const chunks = await drain(stream);
    await flush();

    expect(chunks).toHaveLength(5);
    const [span] = capture.spans();
    expect(span).toMatchObject({
      model: "gpt-4o-mini-2024-07-18",
      usage: { input_tokens: 20, output_tokens: 3, cached_tokens: 0, service_tier: "flex" },
      output: { role: "assistant", content: "Hello there" },
      finish_reason: "stop",
    });
    expect(span?.time_to_first_token_ms).toBeGreaterThanOrEqual(0);
    expect(span?.attributes).toMatchObject({ "request.stream": true });
  });

  it("leaves the output count unknown when the stream carries no usage", async () => {
    const capture = captureSpanlight();
    const openai = client(mockProvider(() => sseResponse(textStream(false))).fetch);

    await drain(
      await openai.chat.completions.create({ model: "gpt-4o-mini", messages: [], stream: true }),
    );
    await flush();

    const [span] = capture.spans();
    expect(span?.usage).toEqual({ service_tier: "flex" });
    expect(span?.attributes).toMatchObject({ "usage.unreported_reason": "include_usage_not_set" });
  });

  it("marks a stream the caller broke out of as client_disconnected", async () => {
    const capture = captureSpanlight();
    const openai = client(mockProvider(() => sseResponse(textStream(true))).fetch);

    const stream = await openai.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [],
      stream: true,
    });
    for await (const event of stream) {
      if (event.choices[0]?.delta.content === "Hello") break;
    }
    await flush();

    const [span] = capture.spans();
    expect(span?.attributes).toMatchObject({ "usage.unreported_reason": "client_disconnected" });
    expect(span?.output).toMatchObject({ content: "Hello" });
  });

  it("traces the .stream() helper once, through create", async () => {
    const capture = captureSpanlight();
    const openai = client(mockProvider(() => sseResponse(textStream(true))).fetch);

    const runner = openai.chat.completions.stream({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "hi" }],
      stream_options: { include_usage: true },
    });
    const final = await runner.finalChatCompletion();
    await flush();

    expect(final.choices[0]?.message.content).toBe("Hello there");
    expect(capture.spans()).toHaveLength(1);
    expect(capture.spans()[0]).toMatchObject({ usage: { input_tokens: 20, output_tokens: 3 } });
  });

  it("re-throws the provider error unchanged and records it on the span", async () => {
    const capture = captureSpanlight();
    const body = {
      error: {
        message: "Rate limit reached for gpt-4o-mini on tokens per min (TPM).",
        type: "tokens",
        param: null,
        code: "rate_limit_exceeded",
      },
    };
    const openai = client(mockProvider(() => jsonResponse(body, 429)).fetch);

    const error = await openai.chat.completions
      .create({ model: "gpt-4o-mini", messages: [], inference_geo: "us" } as never)
      .catch((caught: unknown) => caught);
    await flush();

    expect(error).toBeInstanceOf(RateLimitError);
    const [span] = capture.spans();
    expect(span).toMatchObject({ status: "error", usage: null });
    expect(span?.status_message).toContain("Rate limit reached");
    expect(span?.attributes).toMatchObject({
      "error.type": "RateLimitError",
      "http.status_code": 429,
      "provider.request_id": "req_mock_123",
    });
    expect(span?.attributes).not.toHaveProperty("usage.pricing_modifier_unknown");
  });
});

describe("wrapOpenAI responses", () => {
  it("records usage, the priority tier, completed web searches and the text", async () => {
    const capture = captureSpanlight();
    const openai = client(mockProvider(() => jsonResponse(responseObject())).fetch);

    const response = await openai.responses.create({ model: "gpt-4.1", input: "Where is it?" });
    await flush();

    expect(response.output_text).toBe("Your order has shipped.");
    const [span] = capture.spans();
    expect(span).toMatchObject({
      name: "responses gpt-4.1",
      model: "gpt-4.1-2025-04-14",
      usage: {
        input_tokens: 36,
        output_tokens: 6,
        cached_tokens: 8,
        service_tier: "priority",
        server_tool_use: { web_search_requests: 1 },
      },
      output: { role: "assistant", content: "Your order has shipped." },
    });
    expect(span?.attributes).toMatchObject({ "response.status": "completed" });
  });

  it("records a stream from its terminal event", async () => {
    const capture = captureSpanlight();
    const inProgress = { ...responseObject("in_progress"), output: [], usage: null };
    const delta = (text: string, n: number) => ({
      type: "response.output_text.delta",
      sequence_number: n,
      item_id: "msg_1",
      output_index: 0,
      content_index: 0,
      delta: text,
      logprobs: [],
    });
    const events: SseEvent[] = [
      ["response.created", { type: "response.created", sequence_number: 0, response: inProgress }],
      ["response.output_text.delta", delta("Your order ", 1)],
      ["response.output_text.delta", delta("has shipped.", 2)],
      [
        "response.completed",
        { type: "response.completed", sequence_number: 3, response: responseObject() },
      ],
    ];
    const openai = client(mockProvider(() => sseResponse(events)).fetch);

    await drain(await openai.responses.create({ model: "gpt-4.1", input: "hi", stream: true }));
    await flush();

    const [span] = capture.spans();
    expect(span).toMatchObject({
      usage: { input_tokens: 36, output_tokens: 6, server_tool_use: { web_search_requests: 1 } },
      output: { content: "Your order has shipped." },
    });
    expect(span?.time_to_first_token_ms).toBeGreaterThanOrEqual(0);
  });
});

describe("wrapOpenAI with Azure", () => {
  it("records an AzureOpenAI client as azure_openai", async () => {
    const capture = captureSpanlight();
    const { AzureOpenAI } = await import("openai");
    const azure = wrapOpenAI(
      new AzureOpenAI({
        apiKey: "test",
        endpoint: "https://example.openai.azure.com",
        apiVersion: "2024-10-21",
        fetch: mockProvider(() => jsonResponse(CHAT_COMPLETION)).fetch,
      }),
    );

    await azure.chat.completions.create({ model: "gpt-5.6", messages: [] });
    await flush();

    expect(capture.spans()[0]).toMatchObject({ provider: "azure_openai" });
  });
});
