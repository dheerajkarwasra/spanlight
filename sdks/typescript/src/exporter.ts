/**
 * Background batching exporter for `POST /v1/traces`.
 *
 * Mirrors the Python SDK's delivery guarantees:
 *
 * 1. Never hurt the host application. `export()` only appends to an in-memory queue; all network
 *    work runs later on the event loop, and nothing here throws into the caller.
 * 2. Bounded memory. The queue holds at most `maxQueue` spans; when it is full the oldest span is
 *    dropped and counted.
 * 3. Deliver what we can. A batch goes out when it reaches `batchSize` spans or after
 *    `flushIntervalMs`. Network errors, `429` and `5xx` are retried with jittered exponential
 *    backoff, honouring `Retry-After`; `413` splits the batch; other `4xx` are logged, not retried.
 *
 * The background timer is unref'd, so a pending batch never keeps the process alive on its own.
 */

import { randomBytes } from "node:crypto";
import { promisify } from "node:util";
import { gzip as gzipCallback } from "node:zlib";

import { log } from "./log.js";
import type { Payload } from "./span.js";
import { VERSION } from "./version.js";

const gzip = promisify(gzipCallback);

const MAX_LOGGED_REJECTIONS = 10;
const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 30_000;

export interface ExporterOptions {
  url: string;
  apiKey: string;
  batchSize: number;
  flushIntervalMs: number;
  maxQueue: number;
  gzip: boolean;
  timeoutMs: number;
  maxRetries: number;
  fetch: typeof fetch;
  /** Waits between retries; resolves early when `signal` aborts. Replaced in tests. */
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  /** A number in [0, 1) that randomizes backoff. Replaced in tests. */
  jitter?: () => number;
}

export class Exporter {
  /** Spans accepted by the server. */
  sentCount = 0;
  /** Spans lost after retries or rejected by the server. */
  failedCount = 0;
  /** Spans discarded because the queue was full. */
  droppedCount = 0;

  private readonly exporterId = randomBytes(16).toString("hex");
  private readonly headers: Record<string, string>;
  private readonly sleep: (ms: number, signal: AbortSignal) => Promise<void>;
  private readonly jitter: () => number;
  private readonly abort = new AbortController();
  private queue: Payload[] = [];
  private timer: NodeJS.Timeout | undefined;
  private sending: Promise<void> | undefined;
  private closed = false;

  constructor(private readonly options: ExporterOptions) {
    this.headers = {
      Authorization: `Bearer ${options.apiKey}`,
      "Content-Type": "application/json",
      "User-Agent": `spanlight-js/${VERSION}`,
    };
    if (options.gzip) this.headers["Content-Encoding"] = "gzip";
    this.sleep = options.sleep ?? abortableSleep;
    this.jitter = options.jitter ?? Math.random;
  }

  /** Enqueue one serialized span. Never blocks and never throws. */
  export(span: Payload): void {
    try {
      if (this.closed) return;
      if (this.queue.length >= this.options.maxQueue) {
        this.queue.shift();
        this.droppedCount += 1;
        if (this.droppedCount === 1) {
          log.warn(`Spanlight queue is full (${this.options.maxQueue} spans); dropping the oldest`);
        }
      }
      this.queue.push(span);
      if (this.queue.length >= this.options.batchSize) this.kick();
      else this.schedule();
    } catch (error) {
      log.debug("Failed to enqueue a span", error);
    }
  }

  /**
   * Send everything queued so far and wait until it is delivered.
   * Resolves `true` when the queue drained in time, `false` otherwise; never rejects.
   */
  async flush(timeoutMs?: number): Promise<boolean> {
    try {
      const drained = (async () => {
        while (this.queue.length > 0 || this.sending !== undefined) {
          this.kick();
          await this.sending;
        }
        return true;
      })();
      return timeoutMs === undefined ? await drained : await withTimeout(drained, timeoutMs, false);
    } catch (error) {
      log.debug("Flush failed", error);
      return false;
    }
  }

  /** Flush (bounded by `timeoutMs`), then stop: retries in progress are abandoned. */
  async shutdown(timeoutMs: number): Promise<void> {
    await this.flush(timeoutMs);
    this.closed = true;
    clearTimeout(this.timer);
    this.abort.abort();
  }

  private schedule(): void {
    if (this.timer !== undefined || this.sending !== undefined) return;
    this.timer = setTimeout(() => this.kick(), this.options.flushIntervalMs);
    this.timer.unref();
  }

  private kick(): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    if (this.sending !== undefined) return;
    this.sending = this.drain()
      .catch((error: unknown) => log.debug("Export loop failed", error))
      .finally(() => {
        this.sending = undefined;
        if (this.queue.length > 0) this.schedule();
      });
  }

  private async drain(): Promise<void> {
    while (this.queue.length > 0) {
      const batch = this.queue.splice(0, this.options.batchSize);
      try {
        await this.send(batch);
      } catch (error) {
        this.failedCount += batch.length;
        log.warn(`Unexpected error exporting ${batch.length} spans`, error);
      }
    }
  }

  /** The `client` object sent beside the spans: cumulative loss totals, never deltas. */
  private counters(): Payload {
    return {
      sdk: "spanlight-js",
      version: VERSION,
      exporter_id: this.exporterId,
      dropped_total: this.droppedCount,
      failed_total: this.failedCount,
    };
  }

  private async send(batch: Payload[]): Promise<void> {
    if (batch.length === 0) return;
    const json = JSON.stringify({ spans: batch, client: this.counters() });
    const body = this.options.gzip ? await gzip(json) : json;

    for (let attempt = 0; ; attempt += 1) {
      let failure: string;
      let retryAfterMs: number | undefined;
      try {
        const response = await this.options.fetch(this.options.url, {
          method: "POST",
          headers: this.headers,
          body,
          signal: AbortSignal.any([AbortSignal.timeout(this.options.timeoutMs), this.abort.signal]),
        });
        const status = response.status;
        if (status >= 200 && status < 300) {
          await this.handleSuccess(response, batch);
          return;
        }
        if (status === 413 && batch.length > 1) {
          await response.body?.cancel();
          const middle = Math.floor(batch.length / 2);
          await this.send(batch.slice(0, middle));
          await this.send(batch.slice(middle));
          return;
        }
        if (status !== 429 && status < 500) {
          this.failedCount += batch.length;
          const text = (await response.text().catch(() => "")).slice(0, 500);
          log.error(`Spanlight rejected a batch of ${batch.length} spans: HTTP ${status} ${text}`);
          return;
        }
        await response.body?.cancel();
        failure = `HTTP ${status}`;
        retryAfterMs = parseRetryAfter(response.headers.get("retry-after"));
      } catch (error) {
        failure = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      }

      if (attempt >= this.options.maxRetries || this.abort.signal.aborted) {
        this.failedCount += batch.length;
        log.warn(`Dropping ${batch.length} spans after ${attempt + 1} attempts (${failure})`);
        return;
      }
      const delay = this.retryDelay(attempt, retryAfterMs);
      log.debug(`Export failed (${failure}); retrying in ${delay} ms`);
      await this.sleep(delay, this.abort.signal);
    }
  }

  private async handleSuccess(response: Response, batch: Payload[]): Promise<void> {
    let result: unknown;
    try {
      result = await response.json();
    } catch {
      this.sentCount += batch.length;
      return;
    }
    const rejected = (result as { rejected?: unknown } | null)?.rejected;
    if (!Array.isArray(rejected) || rejected.length === 0) {
      this.sentCount += batch.length;
      return;
    }
    this.sentCount += batch.length - rejected.length;
    this.failedCount += rejected.length;
    log.warn(`Spanlight rejected ${rejected.length} of ${batch.length} spans`);
    for (const item of rejected.slice(0, MAX_LOGGED_REJECTIONS) as Payload[]) {
      log.warn(
        `  span ${String(item.span_id)} (index ${String(item.index)}): ${String(item.reason)}`,
      );
    }
  }

  /**
   * Milliseconds before retry number `attempt + 1`. `Retry-After` wins; otherwise "equal jitter"
   * backoff: half the exponential step is fixed and half random, so a retry is never immediate.
   */
  private retryDelay(attempt: number, retryAfterMs: number | undefined): number {
    if (retryAfterMs !== undefined) return Math.min(retryAfterMs, MAX_BACKOFF_MS);
    const step = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** attempt);
    return step / 2 + (this.jitter() * step) / 2;
  }
}

/** A `Retry-After` header (delta-seconds or HTTP-date) in milliseconds, or undefined. */
export function parseRetryAfter(value: string | null): number | undefined {
  if (value === null) return undefined;
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  const seconds = Number(trimmed);
  if (!Number.isNaN(seconds))
    return Number.isFinite(seconds) ? Math.max(seconds, 0) * 1000 : undefined;
  const moment = Date.parse(trimmed);
  return Number.isNaN(moment) ? undefined : Math.max(moment - Date.now(), 0);
}

/**
 * Unref'd like the batch timer: a retry wait never holds the process open, and the
 * `beforeExit` shutdown then bounds what is left by its timeout.
 */
function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms);
    timer.unref();
    signal.addEventListener("abort", done, { once: true });
    function done(): void {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
  });
}

function withTimeout<T>(promise: Promise<T>, ms: number, fallback: T): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<T>((resolve) => {
    timer = setTimeout(() => resolve(fallback), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}
