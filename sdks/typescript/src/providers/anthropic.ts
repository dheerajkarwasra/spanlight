/**
 * Instrumentation for the official `@anthropic-ai/sdk` package: `messages.create`, plain and
 * `stream: true`. The `messages.stream()` helper calls `create` on the same resource, so it is
 * traced too. Reading follows the Python SDK's `integrations/_anthropic_message.py` and
 * `_anthropic_stream.py`.
 *
 * Anthropic reports `input_tokens` excluding prompt-cache reads and writes; spans record the
 * whole prompt (uncached + cache reads + cache writes) so every provider means the same thing.
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

const PROVIDER = "anthropic";

/**
 * Instrument an `Anthropic` client in place and return it. Every `messages.create` call
 * (plain, `stream: true`, or through `messages.stream()`) becomes an `llm` span. Errors are
 * recorded and reach your code unchanged. Wrapping the same client twice is harmless.
 */
export function wrapAnthropic<T>(client: T): T {
  try {
    patchCreate(get(client, "messages"), messagesEndpoint);
  } catch (error) {
    log.warn("Could not instrument the Anthropic client", error);
  }
  return client;
}

/** Anthropic `usage` as span usage, or undefined when absent. */
function usageFields(usage: unknown): Usage | undefined {
  if (usage === undefined || usage === null) return undefined;
  const uncached = asInt(get(usage, "input_tokens"));
  const cacheRead = asInt(get(usage, "cache_read_input_tokens"));
  const cacheWrite = asInt(get(usage, "cache_creation_input_tokens"));
  let cacheWrite1h = asInt(get(usage, "cache_creation", "ephemeral_1h_input_tokens"));
  if (cacheWrite1h !== undefined && (cacheWrite === undefined || cacheWrite1h > cacheWrite)) {
    // The server rejects the whole span for an inconsistent split; keep the total, drop the part.
    log.debug(
      `Ignoring Anthropic 1h cache-write count ${cacheWrite1h} over total ${String(cacheWrite)}`,
    );
    cacheWrite1h = undefined;
  }
  return {
    inputTokens:
      uncached === undefined ? undefined : uncached + (cacheRead ?? 0) + (cacheWrite ?? 0),
    outputTokens: asInt(get(usage, "output_tokens")),
    cachedTokens: cacheRead,
    cacheWriteTokens: cacheWrite,
    cacheWrite1hTokens: cacheWrite1h,
    serviceTier: asStr(get(usage, "service_tier")),
    webSearchRequests: asInt(get(usage, "server_tool_use", "web_search_requests")),
  };
}

function recordUsage(span: Span, usage: unknown): void {
  const fields = usageFields(usage);
  if (fields !== undefined) span.setUsage(fields);
}

function recordMessageMetadata(span: Span, messageId: unknown, stopReason: unknown): void {
  if (typeof messageId === "string") span.setAttribute("response.id", messageId);
  if (typeof stopReason === "string") {
    span.setAttribute("response.stop_reason", stopReason);
    span.setFinishReason(stopReason);
  }
}

const messagesEndpoint: Endpoint = {
  provider: PROVIDER,
  operation: "chat",
  recordResponse(span, response) {
    span.setModel(asStr(get(response, "model")), PROVIDER);
    recordUsage(span, get(response, "usage"));
    recordMessageMetadata(span, get(response, "id"), get(response, "stop_reason"));
    span.setOutput({ role: "assistant", content: get(response, "content") ?? [] });
  },
  newAccumulator: () => new MessagesStreamAccumulator(),
};

const USAGE_COUNTS = [
  "input_tokens",
  "output_tokens",
  "cache_read_input_tokens",
  "cache_creation_input_tokens",
];

/** Rebuilds a `Message` from raw stream events. */
class MessagesStreamAccumulator implements StreamAccumulator {
  private model: string | undefined;
  private messageId: string | undefined;
  private stopReason: string | undefined;
  private readonly usage: Obj = {};
  // Set by the `message_delta` carrying the final output count; without it the stream was cut.
  private finalUsageSeen = false;
  private readonly blocks = new Map<number, Obj>();
  private readonly partialJson = new Map<number, string[]>();

  onEvent(event: unknown, span: Span): void {
    switch (get(event, "type")) {
      case "message_start": {
        const message = get(event, "message");
        this.model = asStr(get(message, "model")) ?? this.model;
        this.messageId = asStr(get(message, "id")) ?? this.messageId;
        this.mergeUsage(get(message, "usage"));
        break;
      }
      case "content_block_start": {
        const block = get(event, "content_block");
        this.blocks.set(
          asInt(get(event, "index")) ?? 0,
          block !== null && typeof block === "object" ? { ...block } : {},
        );
        break;
      }
      case "content_block_delta":
        span.markFirstToken();
        this.applyDelta(asInt(get(event, "index")) ?? 0, get(event, "delta"));
        break;
      case "message_delta": {
        this.stopReason = asStr(get(event, "delta", "stop_reason")) ?? this.stopReason;
        const usage = get(event, "usage");
        if (asInt(get(usage, "output_tokens")) !== undefined) this.finalUsageSeen = true;
        this.mergeUsage(usage);
        break;
      }
    }
  }

  /** Later events carry cumulative values, so they replace rather than add. */
  private mergeUsage(usage: unknown): void {
    for (const key of USAGE_COUNTS) {
      const value = asInt(get(usage, key));
      if (value !== undefined) this.usage[key] = value;
    }
    const cacheWrite1h = asInt(get(usage, "cache_creation", "ephemeral_1h_input_tokens"));
    if (cacheWrite1h !== undefined)
      this.usage.cache_creation = { ephemeral_1h_input_tokens: cacheWrite1h };
    const tier = asStr(get(usage, "service_tier"));
    if (tier !== undefined) this.usage.service_tier = tier;
    const searches = asInt(get(usage, "server_tool_use", "web_search_requests"));
    if (searches !== undefined) this.usage.server_tool_use = { web_search_requests: searches };
  }

  private applyDelta(index: number, delta: unknown): void {
    let block = this.blocks.get(index);
    if (block === undefined) {
      block = { type: "text", text: "" };
      this.blocks.set(index, block);
    }
    switch (get(delta, "type")) {
      case "text_delta":
        block.text = (asStr(block.text) ?? "") + (asStr(get(delta, "text")) ?? "");
        break;
      case "thinking_delta":
        block.thinking = (asStr(block.thinking) ?? "") + (asStr(get(delta, "thinking")) ?? "");
        break;
      case "input_json_delta": {
        const parts = this.partialJson.get(index) ?? [];
        parts.push(asStr(get(delta, "partial_json")) ?? "");
        this.partialJson.set(index, parts);
        break;
      }
    }
  }

  finish(span: Span, end: StreamEnd): void {
    span.setModel(this.model, PROVIDER);
    if (Object.keys(this.usage).length > 0) recordUsage(span, this.usage);
    if (!this.finalUsageSeen) {
      // The prompt counts from `message_start` stand; its output count is a placeholder.
      span.clearOutputTokens();
      span.setAttribute("usage.unreported_reason", unreportedReason(end, "no_final_usage"));
    }
    recordMessageMetadata(span, this.messageId, this.stopReason);
    for (const [index, parts] of this.partialJson) {
      const block = this.blocks.get(index);
      if (block !== undefined) block.input = parseJson(parts.join(""));
    }
    const content = [...this.blocks.entries()].sort(([a], [b]) => a - b).map(([, block]) => block);
    span.setOutput({ role: "assistant", content });
  }
}

function parseJson(raw: string): unknown {
  if (!raw) return {};
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return raw;
  }
}
