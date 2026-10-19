/**
 * Instrumentation for the official `openai` package: `chat.completions.create` and
 * `responses.create`, plain and streamed. The SDK's `.stream()` and `.parse()` helpers call
 * `create` on the same resource, so they are traced too. Reading follows the Python SDK's
 * `integrations/_openai_response.py` and `_openai_stream.py`.
 */

import { log } from "../log.js";
import type { Span, Usage } from "../span.js";
import {
  asInt,
  asStr,
  type Endpoint,
  get,
  type Obj,
  patchCreate,
  type StreamAccumulator,
  type StreamEnd,
  unreportedReason,
} from "./common.js";

export interface WrapOpenAIOptions {
  /**
   * Provider recorded on spans. Defaults to `azure_openai` for an `AzureOpenAI` client (Azure
   * bills its own rates) and `openai` otherwise; set it when pointing the SDK at a compatible API.
   */
  provider?: string;
}

/**
 * Instrument an `OpenAI` (or `AzureOpenAI`) client in place and return it. Every
 * `chat.completions.create` and `responses.create` call becomes an `llm` span. Errors are
 * recorded and reach your code unchanged. Wrapping the same client twice is harmless.
 */
export function wrapOpenAI<T>(client: T, options: WrapOpenAIOptions = {}): T {
  try {
    const provider = options.provider ?? (isAzure(client) ? "azure_openai" : "openai");
    patchCreate(get(client, "chat", "completions"), chatEndpoint(provider));
    patchCreate(get(client, "responses"), responsesEndpoint(provider));
  } catch (error) {
    log.warn("Could not instrument the OpenAI client", error);
  }
  return client;
}

function isAzure(client: unknown): boolean {
  let proto: unknown = Object.getPrototypeOf(client);
  while (proto !== null && typeof proto === "object") {
    if ((proto as { constructor?: { name?: string } }).constructor?.name === "AzureOpenAI") {
      return true;
    }
    proto = Object.getPrototypeOf(proto);
  }
  return false;
}

/** Chat or Responses `usage` as span usage; both count cached tokens and writes in the prompt. */
function usageFields(usage: unknown): Usage {
  if (get(usage, "prompt_tokens") !== undefined) {
    return {
      inputTokens: asInt(get(usage, "prompt_tokens")),
      outputTokens: asInt(get(usage, "completion_tokens")),
      cachedTokens: asInt(get(usage, "prompt_tokens_details", "cached_tokens")),
      cacheWriteTokens: asInt(get(usage, "prompt_tokens_details", "cache_write_tokens")),
    };
  }
  return {
    inputTokens: asInt(get(usage, "input_tokens")),
    outputTokens: asInt(get(usage, "output_tokens")),
    cachedTokens: asInt(get(usage, "input_tokens_details", "cached_tokens")),
    cacheWriteTokens: asInt(get(usage, "input_tokens_details", "cache_write_tokens")),
  };
}

function recordUsage(span: Span, usage: unknown, detailsKey: string): void {
  if (usage === undefined || usage === null) return;
  span.setUsage(usageFields(usage));
  const reasoning = asInt(get(usage, detailsKey, "reasoning_tokens"));
  if (reasoning) span.setAttribute("usage.reasoning_tokens", reasoning);
}

/** The top-level `service_tier` OpenAI answered with (not inside `usage`). */
function recordServiceTier(span: Span, response: unknown): void {
  const tier = asStr(get(response, "service_tier"));
  if (tier !== undefined) span.setUsage({ serviceTier: tier });
}

function recordResponseId(span: Span, response: unknown): void {
  const id = asStr(get(response, "id"));
  if (id !== undefined) span.setAttribute("response.id", id);
}

// ---------------------------------------------------------------------------------------------
// Chat Completions
// ---------------------------------------------------------------------------------------------

function chatEndpoint(provider: string): Endpoint {
  return {
    provider,
    operation: "chat",
    // Chat Completions report searches as message annotations, which are not a count of billed
    // searches, so the fee cannot be priced from the response.
    requestAttributes: (body: Obj) =>
      body.web_search_options === undefined
        ? {}
        : { "usage.fee_unknown": "openai_chat_web_search" },
    recordResponse(span, response) {
      span.setModel(asStr(get(response, "model")), provider);
      recordUsage(span, get(response, "usage"), "completion_tokens_details");
      recordServiceTier(span, response);
      recordResponseId(span, response);
      const choices = (get(response, "choices") as unknown[] | undefined) ?? [];
      const finishReasons = choices.map((choice) => get(choice, "finish_reason") ?? null);
      if (finishReasons.length > 0) {
        span.setAttribute("response.finish_reasons", finishReasons);
        span.setFinishReason(asStr(finishReasons[0]));
      }
      const messages = choices.map((choice) => get(choice, "message"));
      span.setOutput(messages.length === 1 ? messages[0] : messages);
    },
    newAccumulator: () => new ChatStreamAccumulator(provider),
  };
}

interface ToolCallState {
  id: string | null;
  type: string;
  function: { name: string; arguments: string };
}

class ChoiceState {
  role = "assistant";
  content: string[] = [];
  refusal: string[] = [];
  toolCalls = new Map<number, ToolCallState>();
  finishReason: string | null = null;

  /** Merge one delta; return whether it carried generated output. */
  apply(delta: unknown): boolean {
    let produced = false;
    const role = asStr(get(delta, "role"));
    if (role) this.role = role;
    const content = asStr(get(delta, "content"));
    if (content) {
      this.content.push(content);
      produced = true;
    }
    const refusal = asStr(get(delta, "refusal"));
    if (refusal) {
      this.refusal.push(refusal);
      produced = true;
    }
    for (const call of (get(delta, "tool_calls") as unknown[] | undefined) ?? []) {
      const index = asInt(get(call, "index")) ?? 0;
      let state = this.toolCalls.get(index);
      if (state === undefined) {
        state = { id: null, type: "function", function: { name: "", arguments: "" } };
        this.toolCalls.set(index, state);
      }
      state.id = asStr(get(call, "id")) ?? state.id;
      state.function.name += asStr(get(call, "function", "name")) ?? "";
      state.function.arguments += asStr(get(call, "function", "arguments")) ?? "";
      produced = true;
    }
    return produced;
  }

  toMessage(): Obj {
    const message: Obj = { role: this.role, content: this.content.join("") || null };
    if (this.refusal.length > 0) message.refusal = this.refusal.join("");
    if (this.toolCalls.size > 0) {
      message.tool_calls = [...this.toolCalls.entries()]
        .sort(([a], [b]) => a - b)
        .map(([, call]) => call);
    }
    return message;
  }
}

/** Rebuilds a chat completion from `ChatCompletionChunk` events. */
class ChatStreamAccumulator implements StreamAccumulator {
  private model: string | undefined;
  private responseId: string | undefined;
  private usage: unknown;
  private serviceTier: string | undefined;
  private readonly choices = new Map<number, ChoiceState>();

  constructor(private readonly provider: string) {}

  onEvent(event: unknown, span: Span): void {
    this.model = asStr(get(event, "model")) || this.model;
    this.responseId = asStr(get(event, "id")) ?? this.responseId;
    const usage = get(event, "usage");
    if (usage !== undefined && usage !== null) this.usage = usage;
    this.serviceTier = asStr(get(event, "service_tier")) ?? this.serviceTier;
    for (const choice of (get(event, "choices") as unknown[] | undefined) ?? []) {
      const index = asInt(get(choice, "index")) ?? 0;
      let state = this.choices.get(index);
      if (state === undefined) {
        state = new ChoiceState();
        this.choices.set(index, state);
      }
      if (state.apply(get(choice, "delta"))) span.markFirstToken();
      const finishReason = asStr(get(choice, "finish_reason"));
      if (finishReason !== undefined) state.finishReason = finishReason;
    }
  }

  finish(span: Span, end: StreamEnd): void {
    span.setModel(this.model, this.provider);
    recordUsage(span, this.usage, "completion_tokens_details");
    if (this.serviceTier !== undefined) span.setUsage({ serviceTier: this.serviceTier });
    if (this.usage === undefined) {
      span.setAttribute("usage.unreported_reason", unreportedReason(end, "include_usage_not_set"));
    }
    if (this.responseId) span.setAttribute("response.id", this.responseId);
    const states = [...this.choices.entries()].sort(([a], [b]) => a - b).map(([, state]) => state);
    if (states.length === 0) return;
    const finishReasons = states.map((state) => state.finishReason);
    span.setAttribute("response.finish_reasons", finishReasons);
    span.setFinishReason(finishReasons[0] ?? undefined);
    const messages = states.map((state) => state.toMessage());
    span.setOutput(messages.length === 1 ? messages[0] : messages);
  }
}

// ---------------------------------------------------------------------------------------------
// Responses API
// ---------------------------------------------------------------------------------------------

function responsesEndpoint(provider: string): Endpoint {
  const endpoint: Endpoint = {
    provider,
    operation: "responses",
    recordResponse(span, response) {
      span.setModel(asStr(get(response, "model")), provider);
      recordResponseId(span, response);
      recordUsage(span, get(response, "usage"), "output_tokens_details");
      recordServiceTier(span, response);
      span.setUsage({ webSearchRequests: webSearchCalls(response) });
      const status = asStr(get(response, "status"));
      if (status !== undefined) span.setAttribute("response.status", status);
      if (status === "failed")
        span.recordError(asStr(get(response, "error", "message")) ?? "response failed");
      span.setOutput(responsesOutput(response));
    },
    newAccumulator: () => new ResponsesStreamAccumulator(endpoint),
  };
  return endpoint;
}

/**
 * Billed web searches of a Responses object: its completed `web_search_call` items. Undefined
 * when the output has no search item, so a call that never searched sends no count.
 */
function webSearchCalls(response: unknown): number | undefined {
  const output = (get(response, "output") as unknown[] | undefined) ?? [];
  const searches = output.filter((item) => get(item, "type") === "web_search_call");
  if (searches.length === 0) return undefined;
  return searches.filter((item) => get(item, "status") === "completed").length;
}

/** A `Response` as an assistant message plus any non-text output items. */
function responsesOutput(response: unknown): Obj {
  const text: string[] = [];
  const items: unknown[] = [];
  for (const item of (get(response, "output") as unknown[] | undefined) ?? []) {
    if (get(item, "type") !== "message") {
      items.push(item);
      continue;
    }
    for (const part of (get(item, "content") as unknown[] | undefined) ?? []) {
      const partText = asStr(get(part, "text"));
      if (partText !== undefined) text.push(partText);
    }
  }
  const output: Obj = { role: "assistant", content: text.join("") || null };
  if (items.length > 0) output.items = items;
  return output;
}

const TERMINAL_EVENTS: ReadonlySet<string> = new Set([
  "response.completed",
  "response.incomplete",
  "response.failed",
]);

/** Tracks Responses stream events; the terminal event carries the full response. */
class ResponsesStreamAccumulator implements StreamAccumulator {
  private finalResponse: unknown;
  private lastUsage: unknown;
  private readonly text: string[] = [];
  private model: string | undefined;
  private error: string | undefined;

  constructor(private readonly endpoint: Endpoint) {}

  onEvent(event: unknown, span: Span): void {
    const type = asStr(get(event, "type"));
    if (type === undefined) return;
    if (type.endsWith(".delta")) span.markFirstToken();
    if (type === "response.output_text.delta") {
      const delta = asStr(get(event, "delta"));
      if (delta !== undefined) this.text.push(delta);
    }
    const response = get(event, "response");
    if (response !== undefined && response !== null) {
      this.model = asStr(get(response, "model")) ?? this.model;
      const usage = get(response, "usage");
      if (usage !== undefined && usage !== null) this.lastUsage = usage;
      if (TERMINAL_EVENTS.has(type)) this.finalResponse = response;
    }
    if (type === "error") this.error = asStr(get(event, "message")) ?? "stream error";
  }

  finish(span: Span, end: StreamEnd): void {
    if (this.finalResponse !== undefined) {
      this.endpoint.recordResponse(span, this.finalResponse);
    } else {
      // Without the terminal event the output count is unknown; record what arrived.
      span.setModel(this.model, this.endpoint.provider);
      span.setOutput({ role: "assistant", content: this.text.join("") || null });
      if (this.lastUsage !== undefined) span.setUsage(usageFields(this.lastUsage));
      span.clearOutputTokens();
      // An `error` event ends the stream as a failure even when the SDK threw nothing.
      const failure = end.error ?? this.error;
      span.setAttribute(
        "usage.unreported_reason",
        unreportedReason({ ...end, error: failure }, "no_final_usage"),
      );
    }
    if (this.error !== undefined) span.recordError(this.error);
  }
}
