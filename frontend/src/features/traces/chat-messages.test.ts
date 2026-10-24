import { describe, expect, it } from "vitest";

import { messageText, parseChatPayload, payloadToClipboardText } from "./chat-messages";

describe("parseChatPayload — OpenAI Chat Completions", () => {
  it("parses a plain message array", () => {
    const messages = parseChatPayload([
      { role: "system", content: "Be brief." },
      { role: "user", content: "Hi" },
    ]);

    expect(messages).toEqual([
      {
        role: "system",
        name: null,
        toolCallId: null,
        parts: [{ type: "text", text: "Be brief." }],
      },
      { role: "user", name: null, toolCallId: null, parts: [{ type: "text", text: "Hi" }] },
    ]);
  });

  it("maps the developer role to system", () => {
    expect(parseChatPayload([{ role: "developer", content: "Rules" }])?.[0]?.role).toBe("system");
  });

  it("parses content parts; inline images keep their data URL, remote ones only a label", () => {
    const messages = parseChatPayload([
      {
        role: "user",
        content: [
          { type: "text", text: "What is this?" },
          { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
          { type: "image_url", image_url: { url: "https://example.com/cat.png" } },
        ],
      },
    ]);

    expect(messages?.[0]?.parts).toEqual([
      { type: "text", text: "What is this?" },
      { type: "image", description: "Inline image (base64)", src: "data:image/png;base64,AAAA" },
      { type: "image", description: "https://example.com/cat.png" },
    ]);
  });

  it("parses assistant tool calls with JSON arguments and tool replies", () => {
    const messages = parseChatPayload([
      {
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "call_1",
            type: "function",
            function: { name: "get_weather", arguments: '{"city":"Paris"}' },
          },
          {
            id: "call_2",
            type: "function",
            function: { name: "broken", arguments: "{not json" },
          },
        ],
      },
      { role: "tool", tool_call_id: "call_1", content: "18°C" },
    ]);

    expect(messages?.[0]?.parts).toEqual([
      { type: "tool_call", id: "call_1", name: "get_weather", arguments: { city: "Paris" } },
      { type: "tool_call", id: "call_2", name: "broken", arguments: "{not json" },
    ]);
    expect(messages?.[1]).toEqual({
      role: "tool",
      name: null,
      toolCallId: "call_1",
      parts: [{ type: "text", text: "18°C" }],
    });
  });

  it("parses the legacy function_call field", () => {
    const messages = parseChatPayload([
      { role: "assistant", content: null, function_call: { name: "lookup", arguments: "{}" } },
    ]);

    expect(messages?.[0]?.parts).toEqual([
      { type: "tool_call", id: null, name: "lookup", arguments: {} },
    ]);
  });

  it("parses a completion response from choices", () => {
    const messages = parseChatPayload({
      id: "chatcmpl-1",
      choices: [{ index: 0, message: { role: "assistant", content: "Hello" } }],
      usage: { prompt_tokens: 3 },
    });

    expect(messages).toEqual([
      { role: "assistant", name: null, toolCallId: null, parts: [{ type: "text", text: "Hello" }] },
    ]);
  });

  it("accepts a { messages } wrapper", () => {
    const messages = parseChatPayload({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "Hi" }],
    });

    expect(messages?.map((message) => message.role)).toEqual(["user"]);
  });

  it("accepts a single message object", () => {
    expect(parseChatPayload({ role: "assistant", content: "Done" })?.[0]?.parts).toEqual([
      { type: "text", text: "Done" },
    ]);
  });
});

describe("parseChatPayload — OpenAI Responses API", () => {
  it("parses message and function_call output items and skips reasoning", () => {
    const messages = parseChatPayload({
      output: [
        { type: "reasoning", summary: [] },
        {
          type: "message",
          role: "assistant",
          content: [{ type: "output_text", text: "Answer" }],
        },
        { type: "function_call", call_id: "fc_1", name: "search", arguments: '{"q":"x"}' },
      ],
    });

    expect(messages).toEqual([
      {
        role: "assistant",
        name: null,
        toolCallId: null,
        parts: [{ type: "text", text: "Answer" }],
      },
      {
        role: "assistant",
        name: null,
        toolCallId: null,
        parts: [{ type: "tool_call", id: "fc_1", name: "search", arguments: { q: "x" } }],
      },
    ]);
  });
});

describe("parseChatPayload — Anthropic Messages", () => {
  it("prepends the system prompt and parses content blocks", () => {
    const messages = parseChatPayload({
      model: "claude-sonnet",
      system: [{ type: "text", text: "You are terse." }],
      messages: [
        { role: "user", content: "Weather in Paris?" },
        {
          role: "assistant",
          content: [
            { type: "text", text: "Let me check." },
            { type: "tool_use", id: "toolu_1", name: "get_weather", input: { city: "Paris" } },
          ],
        },
        {
          role: "user",
          content: [
            {
              type: "tool_result",
              tool_use_id: "toolu_1",
              content: [{ type: "text", text: "18°C" }],
            },
            {
              type: "tool_result",
              tool_use_id: "toolu_2",
              content: "boom",
              is_error: true,
            },
          ],
        },
      ],
    });

    expect(messages?.map((message) => message.role)).toEqual([
      "system",
      "user",
      "assistant",
      "user",
    ]);
    expect(messages?.[0]?.parts).toEqual([{ type: "text", text: "You are terse." }]);
    expect(messages?.[2]?.parts).toEqual([
      { type: "text", text: "Let me check." },
      { type: "tool_call", id: "toolu_1", name: "get_weather", arguments: { city: "Paris" } },
    ]);
    expect(messages?.[3]?.parts).toEqual([
      { type: "tool_result", toolCallId: "toolu_1", content: "18°C", isError: false },
      { type: "tool_result", toolCallId: "toolu_2", content: "boom", isError: true },
    ]);
  });

  it("describes base64 images by media type and keeps them as a data URL", () => {
    const messages = parseChatPayload({
      messages: [
        {
          role: "user",
          content: [
            { type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } },
          ],
        },
      ],
    });

    expect(messages?.[0]?.parts).toEqual([
      {
        type: "image",
        description: "Inline image (image/png, base64)",
        src: "data:image/png;base64,AAAA",
      },
    ]);
  });

  it("parses a response message", () => {
    const messages = parseChatPayload({
      id: "msg_1",
      type: "message",
      role: "assistant",
      content: [{ type: "text", text: "Hi there" }],
      stop_reason: "end_turn",
    });

    expect(messages).toEqual([
      {
        role: "assistant",
        name: null,
        toolCallId: null,
        parts: [{ type: "text", text: "Hi there" }],
      },
    ]);
  });

  it("parses a bare response body without role or type", () => {
    const messages = parseChatPayload({
      content: [{ type: "text", text: "Go to Settings." }],
      stop_reason: "end_turn",
    });

    expect(messages).toEqual([
      {
        role: "assistant",
        name: null,
        toolCallId: null,
        parts: [{ type: "text", text: "Go to Settings." }],
      },
    ]);
  });

  it("does not treat arbitrary content arrays as messages", () => {
    expect(parseChatPayload({ content: ["a", "b"] })).toBeNull();
    expect(parseChatPayload({ content: [] })).toBeNull();
  });

  it("reads thinking blocks as thinking parts", () => {
    const messages = parseChatPayload([
      { role: "assistant", content: [{ type: "thinking", thinking: "hmm", signature: "c2ln" }] },
    ]);

    expect(messages?.[0]?.parts).toEqual([{ type: "thinking", text: "hmm" }]);
  });

  it("reads redacted thinking as a redacted part", () => {
    const messages = parseChatPayload([
      { role: "assistant", content: [{ type: "redacted_thinking", data: "opaque" }] },
    ]);

    expect(messages?.[0]?.parts).toEqual([{ type: "redacted_thinking" }]);
  });

  it("drops empty and signature-only thinking blocks", () => {
    const messages = parseChatPayload([
      {
        role: "assistant",
        content: [
          { type: "thinking", thinking: "", signature: "c2ln" },
          { type: "thinking", signature: "c2ln" },
          { type: "text", text: "Done." },
        ],
      },
    ]);

    expect(messages?.[0]?.parts).toEqual([{ type: "text", text: "Done." }]);
  });

  it("reads a top-level content-block list as one assistant message", () => {
    const messages = parseChatPayload([
      { type: "thinking", thinking: "Check the policy." },
      { type: "text", text: "Replacement started." },
    ]);

    expect(messages).toEqual([
      {
        role: "assistant",
        name: null,
        toolCallId: null,
        parts: [
          { type: "thinking", text: "Check the policy." },
          { type: "text", text: "Replacement started." },
        ],
      },
    ]);
  });

  it("does not read a list of unknown typed objects as a message", () => {
    expect(parseChatPayload([{ type: "doc", id: "kb-112" }])).toBeNull();
  });

  it("keeps unknown blocks as JSON parts", () => {
    const messages = parseChatPayload([
      { role: "assistant", content: [{ type: "citation", ref: 1 }] },
    ]);

    expect(messages?.[0]?.parts).toEqual([{ type: "json", value: { type: "citation", ref: 1 } }]);
  });
});

describe("parseChatPayload — OpenTelemetry GenAI", () => {
  it("parses messages with parts", () => {
    const messages = parseChatPayload([
      { role: "user", parts: [{ type: "text", content: "Hello" }] },
    ]);

    expect(messages?.[0]?.parts).toEqual([{ type: "text", text: "Hello" }]);
  });
});

describe("parseChatPayload — unrecognised payloads", () => {
  it.each([
    ["null", null],
    ["a string", "plain text"],
    ["a number", 42],
    ["an empty array", []],
    ["an array of strings", ["a", "b"]],
    ["an unknown role", [{ role: "narrator", content: "x" }]],
    ["a role without content", [{ role: "user" }]],
    ["mixed valid and invalid items", [{ role: "user", content: "hi" }, { foo: 1 }]],
    ["an arbitrary object", { query: "x", top_k: 3 }],
    ["content that is an object", [{ role: "user", content: { a: 1 } }]],
    ["an empty choices list", { choices: [] }],
  ])("returns null for %s", (_label, payload) => {
    expect(parseChatPayload(payload)).toBeNull();
  });
});

describe("messageText", () => {
  it("joins text parts and ignores other parts", () => {
    const [message] =
      parseChatPayload([
        {
          role: "assistant",
          content: [
            { type: "text", text: " First " },
            { type: "tool_use", id: "t", name: "x", input: {} },
            { type: "text", text: "Second" },
          ],
        },
      ]) ?? [];

    expect(message && messageText(message)).toBe("First\n\nSecond");
  });

  it("returns null when there is no text", () => {
    const [message] =
      parseChatPayload([{ role: "assistant", content: null, tool_calls: [] }]) ?? [];

    expect(message && messageText(message)).toBeNull();
  });
});

describe("payloadToClipboardText", () => {
  it("copies strings verbatim and everything else as pretty JSON", () => {
    expect(payloadToClipboardText("hello")).toBe("hello");
    expect(payloadToClipboardText({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(payloadToClipboardText(null)).toBe("null");
    expect(payloadToClipboardText(undefined)).toBe("null");
  });
});
