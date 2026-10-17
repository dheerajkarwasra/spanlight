import type { Span } from "@/lib/api";

/*
 * Reads the provider and gateway attributes a span carries (`provider.*`, `spanlight.gateway.*`)
 * for the provider error block. Attributes are ordinary span data: an old server, an OTLP client
 * or a hand-written payload can send any shape, so every reader here returns null or an empty list for a value it
 * does not recognise, and values are only ever rendered as text.
 */

export const ATTEMPT_LOG_KEY = "spanlight.gateway.attempt_log";
const ATTEMPTS_KEY = "spanlight.gateway.attempts";
const SNAPSHOT_KEY = "provider.ratelimit";
const REQUEST_ID_KEY = "provider.request_id";
const PROVIDER_RETRY_AFTER_KEY = "provider.retry_after_s";
const GATEWAY_RETRY_AFTER_KEY = "spanlight.gateway.retry_after_s";
const GATEWAY_STATUS_KEY = "spanlight.gateway.status";
const GATEWAY_ERROR_CODE_KEY = "spanlight.gateway.error_code";

/** The headers that say when to retry; listed before the rest of the snapshot. */
const RETRY_HEADERS = ["retry-after", "retry-after-ms"];

type Attributes = Record<string, unknown>;

export interface AttemptRow {
  /** The attempt's number in the call; the last kept entry of a cut log has the true count. */
  number: number;
  /** The route target's index (0-based). */
  target: number | null;
  /** The target's credential name. */
  name: string | null;
  /** The provider status ("429") or the attempt's error kind ("timeout"). */
  result: string | null;
  durationMs: number | null;
  retryAfterS: number | null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function isRecord(value: unknown): value is Attributes {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toRow(entry: Attributes, number: number): AttemptRow {
  const status = finiteNumber(entry.status);
  return {
    number,
    target: finiteNumber(entry.target),
    name: text(entry.name),
    result: status !== null ? String(status) : text(entry.error),
    durationMs: finiteNumber(entry.duration_ms),
    retryAfterS: finiteNumber(entry.retry_after_s),
  };
}

/** The true number of attempts: the gateway's count, never fewer than the rows kept. */
export function attemptTotal(attributes: Attributes, rows: readonly AttemptRow[]): number {
  const count = finiteNumber(attributes[ATTEMPTS_KEY]);
  return count !== null && count > rows.length ? Math.round(count) : rows.length;
}

/**
 * The gateway's attempt log, one row per kept attempt. A log of more than 10 attempts keeps the
 * first 9 and the last, so the last row is numbered with the true count. A missing or malformed
 * log gives no rows; a malformed entry is skipped.
 */
export function attemptRows(attributes: Attributes): AttemptRow[] {
  const log = attributes[ATTEMPT_LOG_KEY];
  if (!Array.isArray(log)) {
    return [];
  }
  const entries = log.filter(isRecord);
  const rows = entries.map((entry, index) => toRow(entry, index + 1));
  const total = attemptTotal(attributes, rows);
  const last = rows.at(-1);
  if (last !== undefined && total > rows.length) {
    last.number = total;
  }
  return rows;
}

/**
 * The rate-limit header snapshot as `[name, value]` text pairs: retry headers first, then by name,
 * so each limit's `-limit`, `-remaining` and `-reset` sit together. Nested values are dropped.
 */
export function snapshotRows(attributes: Attributes): [string, string][] {
  const snapshot = attributes[SNAPSHOT_KEY];
  if (!isRecord(snapshot)) {
    return [];
  }
  const rows: [string, string][] = [];
  for (const [name, value] of Object.entries(snapshot)) {
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      rows.push([name, String(value)]);
    }
  }
  const rank = (name: string) => {
    const index = RETRY_HEADERS.indexOf(name);
    return index === -1 ? RETRY_HEADERS.length : index;
  };
  return rows.sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

/** "12 s", "2.5 s": seconds with at most one decimal and a no-break space before the unit. */
export function formatSeconds(seconds: number): string {
  return `${String(Number(seconds.toFixed(1)))}\u00a0s`;
}

/** The provider's retry-after in seconds, else the gateway's; null when neither is a number. */
export function retryAfterSeconds(attributes: Attributes): number | null {
  return (
    finiteNumber(attributes[PROVIDER_RETRY_AFTER_KEY]) ??
    finiteNumber(attributes[GATEWAY_RETRY_AFTER_KEY])
  );
}

export function requestIdOf(attributes: Attributes): string | null {
  return text(attributes[REQUEST_ID_KEY]);
}

/** The status Spanlight's gateway answered with; null for a span that did not pass through it. */
export function gatewayStatusOf(attributes: Attributes): number | null {
  return finiteNumber(attributes[GATEWAY_STATUS_KEY]);
}

/** The gateway's own error code (`BUDGET_EXCEEDED`, `RATE_LIMITED`); null when it made none. */
export function gatewayErrorCodeOf(attributes: Attributes): string | null {
  return text(attributes[GATEWAY_ERROR_CODE_KEY]);
}

type FailureSpan = Pick<Span, "status" | "attributes" | "error_class" | "rate_limit_kind">;

/**
 * Whether Spanlight's gateway made this failure itself rather than relaying the provider's: it
 * stored its own error code, or the failure is classed `gateway` or is the gateway key's limit.
 */
export function isGatewayError(span: FailureSpan): boolean {
  if (span.status !== "error") {
    return false;
  }
  return (
    gatewayErrorCodeOf(span.attributes) !== null ||
    span.error_class === "gateway" ||
    span.rate_limit_kind === "gateway"
  );
}

/**
 * The block shows for a failed LLM call, and for a gateway call that needed more than one attempt
 * (a retried or fallen-back call that still succeeded).
 */
export function showsProviderErrorBlock(span: Pick<Span, "kind" | "status" | "attributes">) {
  if (span.kind !== "llm") {
    return false;
  }
  return span.status === "error" || attemptRows(span.attributes).length > 1;
}

type BlockSpan = Pick<
  Span,
  | "kind"
  | "status"
  | "attributes"
  | "provider_error_type"
  | "provider_error_code"
  | "error_class"
  | "rate_limit_kind"
>;

/**
 * The raw keys the block renders for this span: only those it read a value from, so a malformed
 * value the block shows as "—" stays visible as data. A span that did not fail shows the attempt
 * log alone, so only the log is taken.
 */
function renderedKeys(span: BlockSpan): Set<string> {
  const attributes = span.attributes;
  const keys = new Set<string>();
  if (attemptRows(attributes).length > 0) {
    keys.add(ATTEMPT_LOG_KEY);
  }
  if (span.status !== "error") {
    return keys;
  }
  const providerRetryAfter = finiteNumber(attributes[PROVIDER_RETRY_AFTER_KEY]);
  const read: [string, boolean][] = [
    ["provider.error.type", span.provider_error_type !== null],
    ["provider.error.code", span.provider_error_code !== null],
    [REQUEST_ID_KEY, requestIdOf(attributes) !== null],
    [SNAPSHOT_KEY, snapshotRows(attributes).length > 0],
    [PROVIDER_RETRY_AFTER_KEY, providerRetryAfter !== null],
    [
      GATEWAY_RETRY_AFTER_KEY,
      providerRetryAfter === null && finiteNumber(attributes[GATEWAY_RETRY_AFTER_KEY]) !== null,
    ],
    [GATEWAY_ERROR_CODE_KEY, gatewayErrorCodeOf(attributes) !== null],
  ];
  for (const [key, shown] of read) {
    if (shown) {
      keys.add(key);
    }
  }
  return keys;
}

/** The attributes the metadata tab lists: without the keys the block already renders. */
export function metadataAttributes(span: BlockSpan): Attributes {
  if (!showsProviderErrorBlock(span)) {
    return span.attributes;
  }
  const hidden = renderedKeys(span);
  return Object.fromEntries(Object.entries(span.attributes).filter(([key]) => !hidden.has(key)));
}
