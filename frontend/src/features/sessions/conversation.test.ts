import { describe, expect, it } from "vitest";

import type { Span, SpanKind } from "@/lib/api";

import { earliestFailedSpan, extractTurnConversation } from "./conversation";

const BASE = Date.parse("2026-10-07T12:00:00.000Z");

interface SpanInit {
  id: string;
  parent?: string | null;
  kind?: SpanKind;
  start?: number;
  input?: unknown;
  output?: unknown;
}

function makeSpan({
  id,
  parent = null,
  kind = "llm",
  start = 0,
  input = null,
  output = null,
}: SpanInit): Span {
  return {
    span_id: id,
    parent_span_id: parent,
    kind,
    name: id,
    status: "ok",
    status_message: null,
    error_class: null,
    started_at: new Date(BASE + start).toISOString(),
    ended_at: new Date(BASE + start + 10).toISOString(),
    duration_ms: 10,
    provider: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    cached_tokens: null,
    cache_write_tokens: null,
    cache_write_1h_tokens: null,
    cost_usd: null,
    pricing_version: null,
    time_to_first_token_ms: null,
    input,
    output,
    attributes: {},
    truncated: false,
    finish_reason: null,
    prompt_version_id: null,
  };
}

describe("extractTurnConversation", () => {
  it("reads the question from the first LLM call and the answer from the last", () => {
    const spans = [
      makeSpan({ id: "root", kind: "chain" }),
      makeSpan({
        id: "llm-2",
        parent: "root",
        start: 50,
        input: [
          { role: "user", content: "Weather in Paris?" },
          { role: "tool", tool_call_id: "c1", content: "18°C" },
        ],
        output: { choices: [{ message: { role: "assistant", content: "It's 18°C in Paris." } }] },
      }),
      makeSpan({
        id: "llm-1",
        parent: "root",
        start: 10,
        input: [
          { role: "system", content: "Be brief." },
          { role: "user", content: "Hi" },
          { role: "assistant", content: "Hello" },
          { role: "user", content: "Weather in Paris?" },
        ],
        output: {
          choices: [
            {
              message: {
                role: "assistant",
                content: null,
                tool_calls: [{ id: "c1", function: { name: "weather", arguments: "{}" } }],
              },
            },
          ],
        },
      }),
    ];

    expect(extractTurnConversation(spans)).toEqual({
      userText: "Weather in Paris?",
      assistantText: "It's 18°C in Paris.",
    });
  });

  it("supports Anthropic request and response shapes", () => {
    const spans = [
      makeSpan({
        id: "llm",
        input: { system: "Be kind.", messages: [{ role: "user", content: "Thanks!" }] },
        output: {
          type: "message",
          role: "assistant",
          content: [{ type: "text", text: "Anytime." }],
        },
      }),
    ];

    expect(extractTurnConversation(spans)).toEqual({
      userText: "Thanks!",
      assistantText: "Anytime.",
    });
  });

  it("doesn't use an earlier call's output when the final call failed", () => {
    const spans = [
      makeSpan({ id: "root", kind: "chain", input: { question: "Reset?" } }),
      makeSpan({
        id: "classify",
        parent: "root",
        start: 10,
        input: [{ role: "user", content: "Reset?" }],
        output: { choices: [{ message: { role: "assistant", content: "account_access" } }] },
      }),
      makeSpan({
        id: "answer",
        parent: "root",
        start: 20,
        input: [{ role: "user", content: "Reset?" }],
        output: { choices: [{ message: { role: "assistant", content: null, tool_calls: [] } }] },
      }),
    ];

    expect(extractTurnConversation(spans)).toEqual({ userText: "Reset?", assistantText: null });
  });

  it("falls back to plain-string payloads on the root span", () => {
    const spans = [
      makeSpan({ id: "root", kind: "chain", input: "What's new?", output: "Release 1.2 shipped." }),
      makeSpan({ id: "tool", parent: "root", kind: "tool", input: { q: "x" } }),
    ];

    expect(extractTurnConversation(spans)).toEqual({
      userText: "What's new?",
      assistantText: "Release 1.2 shipped.",
    });
  });

  it("returns nulls when nothing readable was captured", () => {
    const spans = [makeSpan({ id: "root", kind: "chain", input: { query: 1 } })];

    expect(extractTurnConversation(spans)).toEqual({ userText: null, assistantText: null });
  });
});

describe("earliestFailedSpan", () => {
  it("returns the failed span that started first", () => {
    const later = { ...makeSpan({ id: "later", start: 50 }), status: "error" as const };
    const earlier = { ...makeSpan({ id: "earlier", start: 20 }), status: "error" as const };
    const ok = makeSpan({ id: "ok", start: 0 });

    expect(earliestFailedSpan([later, ok, earlier])?.span_id).toBe("earlier");
  });

  it("returns null when nothing failed", () => {
    expect(earliestFailedSpan([makeSpan({ id: "a" })])).toBeNull();
  });
});
