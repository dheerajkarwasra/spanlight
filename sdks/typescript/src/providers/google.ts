/** Instrument the Google Gen AI SDK's unary and streaming Generate Content methods. */

import { log, safely } from "../log.js";
import type { Span } from "../span.js";
import {
  asInt,
  asStr,
  finishWithError,
  get,
  type Obj,
  startLlmSpan,
  type StreamEnd,
  unreportedReason,
} from "./common.js";

const WRAPPED = Symbol.for("spanlight.wrapped");
const SURFACE = "generate_content";
const REQUEST_FIELDS = new Set([
  "tools",
  "toolConfig",
  "safetySettings",
  "cachedContent",
  "labels",
]);
const SDK_CONFIG = new Set(["httpOptions", "abortSignal"]);

export interface WrapGoogleGenAIOptions {
  provider?: string;
}

/** Trace `client.models` in place and return the original Google client. */
export function wrapGoogleGenAI<T>(client: T, options: WrapGoogleGenAIOptions = {}): T {
  try {
    const models = get(client, "models") as Obj | undefined;
    if (models) {
      const provider = options.provider ?? "google";
      patch(models, "generateContent", provider, false);
      patch(models, "generateContentStream", provider, true);
    }
  } catch (error) {
    log.warn("Could not instrument the Google Gen AI client", error);
  }
  return client;
}

function patch(models: Obj, method: string, provider: string, streaming: boolean): void {
  const candidate = models[method];
  if (typeof candidate !== "function" || (candidate as { [WRAPPED]?: boolean })[WRAPPED]) return;
  const original = candidate as (this: unknown, ...args: unknown[]) => Promise<unknown>;
  const wrapped = async function (this: unknown, ...args: unknown[]): Promise<unknown> {
    let request: Obj | undefined;
    safely("prepare Gemini span input", () => {
      request = requestBody(args[0], streaming);
    });
    const span = startLlmSpan({ provider, operation: SURFACE }, request);
    if (!span) return original.apply(this, args);
    span.setAttribute("spanlight.gateway.surface", SURFACE);
    let response: unknown;
    try {
      response = await original.apply(this, args);
    } catch (error) {
      finishWithError(span, error);
      throw error;
    }
    if (streaming) {
      const count = asInt(get(request, "generationConfig", "candidateCount")) ?? 1;
      safely("observe Gemini stream", () =>
        observeGeminiStream(response as Generator, span, count),
      );
    } else {
      safely("record Gemini response", () => recordResponse(span, response));
      span.end();
    }
    return response;
  };
  Object.defineProperty(wrapped, WRAPPED, { value: true });
  models[method] = wrapped;
}

function partsOf(value: unknown): unknown[] {
  const parts: unknown[] = Array.isArray(value) ? value : [value];
  return parts.map((part) => (typeof part === "string" ? { text: part } : part));
}

function contentOf(value: unknown): unknown {
  if (Array.isArray(get(value, "parts"))) return value;
  return { role: "user", parts: partsOf(value) };
}

function requestBody(params: unknown, streaming: boolean): Obj {
  const contents: unknown = get(params, "contents");
  const body: Obj = {
    model: get(params, "model"),
    contents:
      Array.isArray(contents) && contents.every((item) => Array.isArray(get(item, "parts")))
        ? contents
        : [contentOf(contents)],
  };
  if (streaming) body.stream = true;

  const config = get(params, "config");
  if (!config || typeof config !== "object") return body;
  const generation = Object.create(null) as Obj;
  for (const [key, value] of Object.entries(config)) {
    if (value === undefined || value === null || SDK_CONFIG.has(key)) continue;
    if (key === "systemInstruction") body.systemInstruction = contentOf(value);
    else if (REQUEST_FIELDS.has(key)) body[key] = value;
    else generation[key] = value;
  }
  if (get(config, "httpOptions", "extraBody") !== undefined) generation.sdkExtraBody = true;
  if (Object.keys(generation).length) body.generationConfig = generation;
  return body;
}

function tokenCount(value: unknown): number | undefined {
  const count = asInt(value);
  return count !== undefined && count >= 0 ? count : undefined;
}

function recordUsage(span: Span, usage: unknown, finished: boolean, blocked: boolean): void {
  const input = tokenCount(get(usage, "promptTokenCount"));
  const output = tokenCount(get(usage, "candidatesTokenCount"));
  const cached = tokenCount(get(usage, "cachedContentTokenCount"));
  const thoughts = tokenCount(get(usage, "thoughtsTokenCount"));
  span.setUsage({
    inputTokens: input,
    outputTokens:
      finished && (output !== undefined || blocked) ? (output ?? 0) + (thoughts ?? 0) : undefined,
    cachedTokens:
      input !== undefined && cached !== undefined && cached <= input ? cached : undefined,
    serviceTier: asStr(get(usage, "serviceTier")),
  });
  if (finished && thoughts !== undefined) span.setAttribute("usage.reasoning_tokens", thoughts);
}

function blockReason(response: unknown): string | undefined {
  const reason = asStr(get(response, "promptFeedback", "blockReason"));
  return reason === "BLOCK_REASON_UNSPECIFIED" ? undefined : reason;
}

function recordResponse(span: Span, response: unknown): void {
  span.setModel(asStr(get(response, "modelVersion")));
  const id = asStr(get(response, "responseId"));
  if (id) span.setAttribute("response.id", id);
  const first = get(response, "candidates", "0");
  const block = blockReason(response);
  span.setFinishReason(asStr(get(first, "finishReason")) ?? block);
  span.setOutput(get(first, "content"));
  recordUsage(span, get(response, "usageMetadata"), true, block !== undefined);
}

class GeminiStream {
  private model?: string;
  private responseId?: string;
  private reason?: string;
  private blocked = false;
  private usage: unknown;
  private finalUsage: unknown;
  private readonly parts: unknown[] = [];
  private readonly finishedCandidates = new Set<number>();

  constructor(private readonly count: number) {}

  observe(event: unknown, span: Span): void {
    this.model = asStr(get(event, "modelVersion")) ?? this.model;
    this.responseId = asStr(get(event, "responseId")) ?? this.responseId;
    const block = blockReason(event);
    if (block) {
      this.reason = block;
      this.blocked = true;
    }
    const candidates = get(event, "candidates");
    if (Array.isArray(candidates)) {
      for (const [position, candidate] of candidates.entries()) {
        const index = asInt(get(candidate, "index")) ?? position;
        const reason = asStr(get(candidate, "finishReason"));
        if (index >= 0 && index < this.count && reason && reason !== "FINISH_REASON_UNSPECIFIED") {
          this.finishedCandidates.add(index);
          if (index === 0) this.reason = reason;
        }
        if (index !== 0) continue;
        const parts = get(candidate, "content", "parts");
        if (Array.isArray(parts) && parts.length) {
          span.markFirstToken();
          for (const part of parts as unknown[]) this.parts.push(part);
        }
      }
    }
    const usage = get(event, "usageMetadata");
    if (usage) {
      this.usage = usage;
      if (this.blocked || this.finishedCandidates.size >= this.count) this.finalUsage = usage;
    }
  }

  finish(span: Span, end: StreamEnd): void {
    span.setModel(this.model);
    if (this.responseId) span.setAttribute("response.id", this.responseId);
    span.setFinishReason(this.reason);
    if (this.parts.length) span.setOutput({ role: "model", parts: this.parts });
    const final = end.error === undefined && this.finalUsage !== undefined;
    recordUsage(span, final ? this.finalUsage : this.usage, final, this.blocked);
    if (span.outputTokens === undefined) {
      span.setAttribute("usage.unreported_reason", unreportedReason(end, "missing_final_usage"));
    }
  }
}

type Generator = AsyncGenerator<unknown, unknown, unknown>;
type Step = (value: unknown) => Promise<IteratorResult<unknown, unknown>>;

/** Observe the original iterator, including a return before the first next. */
function observeGeminiStream(stream: Generator, span: Span, count: number): void {
  const accumulator = new GeminiStream(count);
  const observeStep =
    (step: Step, closedEarly: boolean) =>
    async (value?: unknown): Promise<IteratorResult<unknown, unknown>> => {
      try {
        const event = await step(value);
        if (!span.isEnded) {
          if (event.done) {
            safely("finish Gemini stream", () => accumulator.finish(span, { closedEarly }));
            span.end();
          } else {
            safely("observe Gemini chunk", () => accumulator.observe(event.value, span));
          }
        }
        return event;
      } catch (error) {
        if (!span.isEnded) {
          safely("finish failed Gemini stream", () => accumulator.finish(span, { error }));
          finishWithError(span, error);
        }
        throw error;
      }
    };
  const next = stream.next.bind(stream);
  const close = stream.return.bind(stream);
  const raise = stream.throw.bind(stream);
  stream.next = observeStep(next, false);
  stream.return = observeStep(close, true);
  stream.throw = observeStep(raise, true);
}
