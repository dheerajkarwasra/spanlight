/**
 * Spans and the trace state they share.
 *
 * A `Span` is one timed operation (an LLM call, a tool call, a whole request). Spans that share a
 * `TraceState` form one trace; the trace state carries trace-level fields such as the user and
 * session, attached to every span when it is exported. `toPayload()` is the span object of
 * `POST /v1/traces` (`SpanIn` in `backend/app/ingest/schemas.py`).
 */

import { randomBytes } from "node:crypto";

import { log } from "./log.js";
import { toJsonable } from "./serialize.js";
import { MAX_RAW_TIER_LENGTH, normalizeTier, type ServiceTier } from "./tiers.js";

export type SpanKind = "llm" | "tool" | "retrieval" | "chain" | "http" | "other";
export type SpanStatus = "ok" | "error" | "unset";
export type Payload = Record<string, unknown>;

/** Token usage. `inputTokens` is the whole prompt: uncached tokens, cache reads and cache writes. */
export interface Usage {
  inputTokens?: number;
  outputTokens?: number;
  /** The part of `inputTokens` read from the provider's prompt cache. */
  cachedTokens?: number;
  /** The part of `inputTokens` written to the prompt cache. */
  cacheWriteTokens?: number;
  /** The part of `cacheWriteTokens` written with a 1-hour TTL (the rest are 5-minute writes). */
  cacheWrite1hTokens?: number;
  /** The provider's spelling (`default`, `priority_on_demand`, `flex`, ...); set the model first. */
  serviceTier?: string;
  /** Server-side web searches the call made. */
  webSearchRequests?: number;
}

const KINDS: ReadonlySet<string> = new Set(["llm", "tool", "retrieval", "chain", "http", "other"]);
const MAX_STATUS_MESSAGE = 2_000;
const MAX_FINISH_REASON = 64;

const COUNT_FIELDS = [
  ["inputTokens", "input_tokens"],
  ["outputTokens", "output_tokens"],
  ["cachedTokens", "cached_tokens"],
  ["cacheWriteTokens", "cache_write_tokens"],
  ["cacheWrite1hTokens", "cache_write_1h_tokens"],
] as const;

type CountField = (typeof COUNT_FIELDS)[number][0];

export function newTraceId(): string {
  return randomHex(16);
}

function newSpanId(): string {
  return randomHex(8);
}

/** Random lowercase hex, never all zeros (invalid in W3C Trace Context and on the server). */
function randomHex(bytes: number): string {
  for (;;) {
    const value = randomBytes(bytes).toString("hex");
    if (/[^0]/.test(value)) return value;
  }
}

/** Unix epoch milliseconds with sub-millisecond precision, from a monotonic clock. */
function nowMs(): number {
  return performance.timeOrigin + performance.now();
}

/** ISO-8601 in UTC with microseconds, the precision the Python SDK sends. */
function isoMicros(epochMs: number): string {
  const whole = Math.floor(epochMs);
  const micros = Math.floor((epochMs - whole) * 1000);
  return new Date(whole).toISOString().replace("Z", `${String(micros).padStart(3, "0")}Z`);
}

export function normalizeKind(kind: string | undefined): SpanKind {
  if (kind === undefined) return "chain";
  if (KINDS.has(kind)) return kind as SpanKind;
  log.debug(`Unknown span kind ${JSON.stringify(kind)}; using 'other'`);
  return "other";
}

/** Trace-level fields, shared by reference by every span of one trace. */
export class TraceState {
  readonly traceId = newTraceId();
  userId: string | undefined;
  sessionId: string | undefined;
  readonly tags: string[] = [];

  constructor(
    /** The root span's name until `updateTrace({ name })` sets another. */
    public name: string | undefined,
    readonly environment: string | undefined,
    readonly release: string | undefined,
  ) {}

  toPayload(): Payload {
    return {
      name: this.name ?? null,
      environment: this.environment ?? null,
      release: this.release ?? null,
      user_id: this.userId ?? null,
      session_id: this.sessionId ?? null,
      tags: [...this.tags],
    };
  }
}

/**
 * One timed operation within a trace. Every setter is safe to call at any time and never throws;
 * calls after `end()` are ignored. A span created while the SDK is disabled records nothing.
 */
export class Span {
  readonly spanId = newSpanId();
  status: SpanStatus = "unset";
  statusMessage: string | undefined;
  provider: string | undefined;
  model: string | undefined;
  inputTokens: number | undefined;
  outputTokens: number | undefined;
  cachedTokens: number | undefined;
  cacheWriteTokens: number | undefined;
  cacheWrite1hTokens: number | undefined;
  serviceTier: ServiceTier | undefined;
  webSearchRequests: number | undefined;
  timeToFirstTokenMs: number | undefined;
  finishReason: string | undefined;
  input: unknown = null;
  output: unknown = null;
  readonly attributes: Record<string, unknown> = {};

  private inputCut = false;
  private outputCut = false;
  private readonly startMs = nowMs();
  private endMs: number | undefined;

  constructor(
    readonly name: string,
    readonly kind: SpanKind,
    readonly trace: TraceState,
    readonly parentSpanId: string | undefined,
    private readonly onEnd: ((span: Span) => void) | undefined,
    readonly recording = true,
  ) {}

  get traceId(): string {
    return this.trace.traceId;
  }

  get isEnded(): boolean {
    return this.endMs !== undefined;
  }

  private get mutable(): boolean {
    return this.recording && this.endMs === undefined;
  }

  setInput(value: unknown): void {
    if (!this.mutable) return;
    ({ value: this.input, cut: this.inputCut } = toJsonable(value));
  }

  setOutput(value: unknown): void {
    if (!this.mutable) return;
    ({ value: this.output, cut: this.outputCut } = toJsonable(value));
  }

  setAttribute(key: string, value: unknown): void {
    if (this.mutable) this.attributes[key] = toJsonable(value).value;
  }

  setAttributes(attributes: Record<string, unknown>): void {
    for (const [key, value] of Object.entries(attributes)) this.setAttribute(key, value);
  }

  /** Record the model, and optionally the provider, that served the call. */
  setModel(model: string | undefined, provider?: string): void {
    if (!this.mutable) return;
    if (model) this.model = model;
    if (provider) this.provider = provider;
  }

  /**
   * Record token usage; an absent field leaves the value unchanged. A count that is not a
   * non-negative integer is ignored, because the server would reject the whole span.
   */
  setUsage(usage: Usage): void {
    if (!this.mutable) return;
    for (const [field] of COUNT_FIELDS) this.setCount(field, usage[field]);
    if (typeof usage.serviceTier === "string") this.setServiceTier(usage.serviceTier);
    if (isCount(usage.webSearchRequests)) this.webSearchRequests = usage.webSearchRequests;
  }

  /** Forget the output count: a stream cut before its final usage has none worth trusting. */
  clearOutputTokens(): void {
    if (this.mutable) this.outputTokens = undefined;
  }

  /** Record why generation stopped, exactly as the provider reports it. */
  setFinishReason(reason: string | undefined): void {
    if (this.mutable && typeof reason === "string" && reason) {
      this.finishReason = reason.slice(0, MAX_FINISH_REASON);
    }
  }

  /** Record time-to-first-token as now; only the first call counts. */
  markFirstToken(): void {
    if (this.mutable && this.timeToFirstTokenMs === undefined) {
      this.timeToFirstTokenMs = Math.max(nowMs() - this.startMs, 0);
    }
  }

  /** Mark the span as failed, with the error or a message. */
  recordError(error: unknown): void {
    if (!this.mutable) return;
    this.status = "error";
    let message: string;
    if (error instanceof Error) {
      message = error.message || error.name;
      this.attributes["error.type"] = error.constructor.name || error.name;
    } else {
      message = typeof error === "string" ? error : String(error);
    }
    this.statusMessage = message.slice(0, MAX_STATUS_MESSAGE);
  }

  /** Finish the span and hand it to the exporter. Idempotent. */
  end(): void {
    if (this.endMs !== undefined) return;
    this.endMs = Math.max(nowMs(), this.startMs);
    if (this.status === "unset") this.status = "ok";
    if (!this.recording || this.onEnd === undefined) return;
    try {
      this.onEnd(this);
    } catch (error) {
      log.debug(`Failed to export span ${this.spanId}`, error);
    }
  }

  toPayload(): Payload {
    const payload: Payload = {
      trace_id: this.traceId,
      span_id: this.spanId,
      parent_span_id: this.parentSpanId ?? null,
      name: this.name,
      kind: this.kind,
      status: this.status,
      status_message: this.statusMessage ?? null,
      start_time: isoMicros(this.startMs),
      end_time: isoMicros(this.endMs ?? nowMs()),
      provider: this.provider ?? null,
      model: this.model ?? null,
      usage: this.usagePayload(),
      time_to_first_token_ms: this.timeToFirstTokenMs ?? null,
      finish_reason: this.finishReason ?? null,
      input: this.input,
      output: this.output,
      attributes: { ...this.attributes },
      trace: this.trace.toPayload(),
    };
    if (this.inputCut || this.outputCut) payload.truncated = true;
    return payload;
  }

  private setCount(field: CountField, value: number | undefined): void {
    if (value === undefined) return;
    if (isCount(value)) this[field] = value;
    else log.debug(`Ignoring invalid ${field} ${String(value)}`);
  }

  private setServiceTier(raw: string): void {
    if (!raw.trim()) return;
    const tier = normalizeTier(this.provider, raw);
    if (tier === undefined) {
      this.attributes["usage.service_tier_raw"] = raw.slice(0, MAX_RAW_TIER_LENGTH);
    } else {
      this.serviceTier = tier;
      // A known tier supersedes an unknown spelling recorded earlier.
      delete this.attributes["usage.service_tier_raw"];
    }
  }

  /** The `usage` object, with only the fields that were set; null when none was. */
  private usagePayload(): Payload | null {
    const usage: Payload = {};
    for (const [field, key] of COUNT_FIELDS) {
      if (this[field] !== undefined) usage[key] = this[field];
    }
    if (this.serviceTier !== undefined) usage.service_tier = this.serviceTier;
    if (this.webSearchRequests !== undefined) {
      usage.server_tool_use = { web_search_requests: this.webSearchRequests };
    }
    return Object.keys(usage).length > 0 ? usage : null;
  }
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}
