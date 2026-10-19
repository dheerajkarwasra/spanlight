/**
 * The global client, the current-span context and the tracing helpers built on them.
 *
 * The current span lives in an `AsyncLocalStorage`, so nested calls, awaited promises and timers
 * started inside a span see it as their parent without any argument passing.
 */

import { AsyncLocalStorage } from "node:async_hooks";

import { Exporter } from "./exporter.js";
import { log } from "./log.js";
import { normalizeKind, Span, type SpanKind, TraceState } from "./span.js";

export interface SpanlightOptions {
  /** Project API key. Defaults to `SPANLIGHT_API_KEY`; without one the SDK is a no-op. */
  apiKey?: string;
  /** Base URL of the Spanlight API. Defaults to `SPANLIGHT_HOST`, then `http://localhost:8000`. */
  host?: string;
  /** Attached to every trace. Defaults to `SPANLIGHT_ENVIRONMENT`. */
  environment?: string;
  /** Your app version or git SHA. Defaults to `SPANLIGHT_RELEASE`. */
  release?: string;
  /** `false` turns tracing into a no-op. Defaults to `SPANLIGHT_ENABLED` (`false`/`0`/`no`/`off`). */
  enabled?: boolean;
  /** Longest a span waits before it is sent. Default 2000. */
  flushIntervalMs?: number;
  /** Spans per request, 1 to 1000. Default 100. */
  batchSize?: number;
  /** Buffered spans; when full, the oldest are dropped. Default 10000. */
  maxQueue?: number;
  /** Gzip request bodies. Default true. */
  gzip?: boolean;
  /** HTTP timeout per request. Default 10000. */
  timeoutMs?: number;
  /** Retries for network errors, 429 and 5xx. Default 5. */
  maxRetries?: number;
  /** Time budget for the flush when the process is about to exit. Default 5000. */
  shutdownTimeoutMs?: number;
  /** The `fetch` used to reach Spanlight. Defaults to the global `fetch`. */
  fetch?: typeof fetch;
}

export interface SpanOptions {
  /** Defaults to `chain`. */
  kind?: SpanKind;
  input?: unknown;
  attributes?: Record<string, unknown>;
}

export interface ObserveOptions {
  /** Span name; defaults to the function's name. */
  name?: string;
  /** Defaults to `chain`. */
  kind?: SpanKind;
  /** Record the arguments as the span input. Default true. */
  captureInput?: boolean;
  /** Record the return value (or resolved value) as the span output. Default true. */
  captureOutput?: boolean;
}

export interface TraceUpdate {
  name?: string;
  userId?: string;
  sessionId?: string;
  /** Tags accumulate across calls. */
  tags?: string[];
}

const DISABLED_VALUES: ReadonlySet<string> = new Set(["false", "0", "no", "off"]);
const context = new AsyncLocalStorage<Span>();

// A loop rather than `/\/+$/`, which backtracks quadratically on a long run of '/'.
export function stripTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === "/") end--;
  return value.slice(0, end);
}

export class Client {
  readonly exporter: Exporter | undefined;
  private readonly environment: string | undefined;
  private readonly release: string | undefined;
  private readonly shutdownTimeoutMs: number;
  private readonly onBeforeExit: (() => void) | undefined;

  constructor(options: SpanlightOptions = {}) {
    const env = process.env;
    this.environment = options.environment ?? (env.SPANLIGHT_ENVIRONMENT || undefined);
    this.release = options.release ?? (env.SPANLIGHT_RELEASE || undefined);
    this.shutdownTimeoutMs = options.shutdownTimeoutMs ?? 5_000;
    const enabled =
      options.enabled ?? !DISABLED_VALUES.has((env.SPANLIGHT_ENABLED ?? "").trim().toLowerCase());
    const apiKey = options.apiKey ?? (env.SPANLIGHT_API_KEY || undefined);
    if (!enabled) return;
    if (!apiKey) {
      log.warn("No API key: set SPANLIGHT_API_KEY or pass apiKey to init(). Tracing is disabled.");
      return;
    }
    const host = stripTrailingSlashes(
      options.host ?? (env.SPANLIGHT_HOST || "http://localhost:8000"),
    );
    this.exporter = new Exporter({
      url: `${host}/v1/traces`,
      apiKey,
      batchSize: Math.min(Math.max(Math.trunc(options.batchSize ?? 100), 1), 1000),
      flushIntervalMs: options.flushIntervalMs ?? 2_000,
      maxQueue: options.maxQueue ?? 10_000,
      gzip: options.gzip ?? true,
      timeoutMs: options.timeoutMs ?? 10_000,
      maxRetries: options.maxRetries ?? 5,
      fetch: options.fetch ?? globalThis.fetch.bind(globalThis),
    });
    // The batch timer is unref'd, so a script reaches `beforeExit` with spans still queued.
    let exiting = false;
    this.onBeforeExit = () => {
      if (exiting) return;
      exiting = true;
      void this.exporter?.shutdown(this.shutdownTimeoutMs);
    };
    process.on("beforeExit", this.onBeforeExit);
  }

  get enabled(): boolean {
    return this.exporter !== undefined;
  }

  /** Start a span under the current span, or as the root of a new trace. End it yourself. */
  startSpan(name: string, kind: SpanKind): Span {
    const exporter = this.exporter;
    if (exporter === undefined)
      return new Span(
        name,
        kind,
        new TraceState(name, undefined, undefined),
        undefined,
        undefined,
        false,
      );
    const parent = context.getStore();
    const trace = parent?.recording
      ? parent.trace
      : new TraceState(name, this.environment, this.release);
    const parentId = parent?.recording ? parent.spanId : undefined;
    return new Span(name, kind, trace, parentId, (span) => exporter.export(span.toPayload()));
  }

  flush(timeoutMs?: number): Promise<boolean> {
    return this.exporter?.flush(timeoutMs) ?? Promise.resolve(true);
  }

  async shutdown(timeoutMs?: number): Promise<void> {
    if (this.onBeforeExit !== undefined) process.off("beforeExit", this.onBeforeExit);
    await this.exporter?.shutdown(timeoutMs ?? this.shutdownTimeoutMs);
  }
}

let globalClient: Client | undefined;

/**
 * Configure the global client used by `observe`, `withSpan` and the provider wrappers.
 * Calling it again replaces the previous client after flushing it in the background.
 */
export function init(options: SpanlightOptions = {}): Client {
  const previous = globalClient;
  globalClient = new Client(options);
  if (previous !== undefined) void previous.shutdown();
  return globalClient;
}

/** The global client, created from the `SPANLIGHT_*` environment variables on first use. */
export function getClient(): Client {
  globalClient ??= new Client();
  return globalClient;
}

/** The span the current code runs in, if any. */
export function currentSpan(): Span | undefined {
  return context.getStore();
}

/** The current trace id (32 hex characters), to put in your logs; undefined outside a trace. */
export function getCurrentTraceId(): string | undefined {
  const span = context.getStore();
  return span?.recording ? span.traceId : undefined;
}

/** Set trace-level fields on the current trace. Outside a span it does nothing. */
export function updateTrace(update: TraceUpdate): void {
  const span = context.getStore();
  if (!span?.recording) return;
  const trace = span.trace;
  if (update.name !== undefined) trace.name = update.name;
  if (update.userId !== undefined) trace.userId = update.userId;
  if (update.sessionId !== undefined) trace.sessionId = update.sessionId;
  for (const tag of update.tags ?? []) {
    if (!trace.tags.includes(tag)) trace.tags.push(tag);
  }
}

/** Send every span ended so far. Resolves `true` when everything was delivered in time. */
export function flush(timeoutMs?: number): Promise<boolean> {
  return getClient().flush(timeoutMs);
}

/** Flush, then stop the exporter. Call it before `process.exit()`. */
export function shutdown(timeoutMs?: number): Promise<void> {
  return getClient().shutdown(timeoutMs);
}

/**
 * Run `fn` inside a new span and end the span when `fn` returns, or when the promise it returns
 * settles. An error `fn` throws or rejects with marks the span failed and is re-thrown unchanged.
 */
export function withSpan<T>(name: string, fn: (span: Span) => T, options: SpanOptions = {}): T {
  const span = getClient().startSpan(name, normalizeKind(options.kind));
  if (options.input !== undefined) span.setInput(options.input);
  if (options.attributes !== undefined) span.setAttributes(options.attributes);
  return runInSpan(span, () => fn(span), false);
}

/**
 * Wrap `fn` so every call is traced as a span: the arguments are its input and the return value
 * (or resolved value) its output. Errors are recorded and re-thrown unchanged.
 */
export function observe<A extends unknown[], R>(
  fn: (...args: A) => R,
  options: ObserveOptions = {},
): (...args: A) => R {
  const name = options.name ?? (fn.name || "anonymous");
  const kind = normalizeKind(options.kind);
  return function observed(this: unknown, ...args: A): R {
    const span = getClient().startSpan(name, kind);
    if (options.captureInput ?? true) span.setInput(args);
    return runInSpan(span, () => fn.apply(this, args), options.captureOutput ?? true);
  };
}

function runInSpan<T>(span: Span, fn: () => T, captureOutput: boolean): T {
  if (!span.recording) return fn();
  let result: T;
  try {
    result = context.run(span, fn);
  } catch (error) {
    span.recordError(error);
    span.end();
    throw error;
  }
  if (!isPromiseLike(result)) {
    if (captureOutput) span.setOutput(result);
    span.end();
    return result;
  }
  return result.then(
    (value) => {
      if (captureOutput) span.setOutput(value);
      span.end();
      return value;
    },
    (error: unknown) => {
      span.recordError(error);
      span.end();
      throw error;
    },
  ) as T;
}

function isPromiseLike(value: unknown): value is PromiseLike<unknown> {
  return typeof (value as { then?: unknown } | null)?.then === "function";
}
