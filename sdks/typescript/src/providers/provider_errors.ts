/**
 * Extract gateway refusals, provider error types/codes, rate-limit headers and Retry-After
 * from thrown provider SDK errors (`openai` and `@anthropic-ai/sdk` `APIStatusError`).
 * Mirrors `sdks/python/src/spanlight/integrations/_provider_errors.py`.
 */

import { log } from "../log.js";
import { type HeaderSource, headerEntries, snapshotHeaders } from "../ratelimit_headers.js";
import type { Span } from "../span.js";
import { asStr, get, type Obj } from "./common.js";

const ERROR_TYPE = "provider.error.type";
const ERROR_CODE = "provider.error.code";
const RATELIMIT = "provider.ratelimit";
const RETRY_AFTER_S = "provider.retry_after_s";
const GATEWAY_ERROR_CODE = "spanlight.gateway.error_code";

export const GATEWAY_CODES: ReadonlySet<string> = new Set([
  "INVALID_REQUEST",
  "NO_COMPATIBLE_TARGET",
  "MODEL_NOT_ALLOWED",
  "PAYLOAD_TOO_LARGE",
  "NOT_FOUND",
  "METHOD_NOT_ALLOWED",
  "UNAUTHORIZED",
  "RATE_LIMITED",
  "BUDGET_EXCEEDED",
  "UPSTREAM_TIMEOUT",
  "UPSTREAM_UNREACHABLE",
  "UPSTREAM_BLOCKED",
  "INTERNAL_ERROR",
  "SERVICE_UNAVAILABLE",
]);

const FAULT_CODE = /^FAULT_[A-Z0-9_]{1,58}$/;
const GATEWAY_CODE_HEADER = "x-spanlight-code";
const UPSTREAM_REQUEST_ID_HEADER = "x-upstream-request-id";
const MAX_ERROR_CHARS = 64;
const MAX_RETRY_AFTER_CHARS = 32;

function getHeader(headers: HeaderSource | undefined | null, name: string): string | undefined {
  if (headers === undefined || headers === null || typeof headers !== "object") return undefined;
  for (const [key, value] of headerEntries(headers)) {
    if (key === name && typeof value === "string") return value;
  }
  return undefined;
}

function getHeaders(error: unknown): HeaderSource | undefined {
  const headers = get(error, "headers") ?? get(error, "response", "headers");
  if (headers !== null && typeof headers === "object") return headers as HeaderSource;
  return undefined;
}

/** Gateway refusal code from `X-Spanlight-Code`, or undefined when upstream answered. */
export function gatewayErrorCode(headers: HeaderSource | undefined | null): string | undefined {
  const raw = getHeader(headers, GATEWAY_CODE_HEADER);
  if (raw === undefined) return undefined;
  const code = raw.trim();
  if (GATEWAY_CODES.has(code) || FAULT_CODE.test(code)) return code;
  return undefined;
}

/** Provider request id for `error`, or undefined on gateway-made refusals. */
export function providerRequestId(error: unknown): string | undefined {
  const headers = getHeaders(error);
  if (gatewayErrorCode(headers) !== undefined) return undefined;
  const upstream = getHeader(headers, UPSTREAM_REQUEST_ID_HEADER);
  if (upstream) return upstream;
  return asStr(get(error, "requestID")) ?? asStr(get(error, "request_id"));
}

/** Parse `retry-after-ms` / `retry-after` in seconds. Mirrors `_provider_errors.parse_retry_after`. */
export function parseRetryAfterSeconds(
  headers: Readonly<Record<string, string>>,
): number | undefined {
  const ms = nonNegativeNumber(headers["retry-after-ms"]);
  if (ms !== undefined) return ms / 1000;
  return nonNegativeNumber(headers["retry-after"]);
}

function nonNegativeNumber(value: string | undefined): number | undefined {
  if (value === undefined || value.length > MAX_RETRY_AFTER_CHARS) return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

function bounded(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 && value.length <= MAX_ERROR_CHARS
    ? value
    : undefined;
}

function errorObject(error: unknown): Obj {
  const body = get(error, "error") ?? get(error, "body");
  if (body === null || typeof body !== "object") return {};
  const inner = (body as Obj).error;
  if (inner !== null && typeof inner === "object") return inner as Obj;
  return body as Obj;
}

/** Record gateway code or provider error details + rate-limit snapshot onto `span`. Never throws. */
export function recordResponseDetails(span: Span, error: unknown): void {
  try {
    const headers = getHeaders(error);
    const gatewayCode = gatewayErrorCode(headers);
    if (gatewayCode !== undefined) {
      span.setAttribute(GATEWAY_ERROR_CODE, gatewayCode);
      return;
    }
    if (headers !== undefined) {
      const snapshot = snapshotHeaders(headers, { failed: true });
      if (Object.keys(snapshot).length > 0) span.setAttribute(RATELIMIT, snapshot);
      const retryAfter = parseRetryAfterSeconds(snapshot);
      if (retryAfter !== undefined) span.setAttribute(RETRY_AFTER_S, retryAfter);
    }
    const obj = errorObject(error);
    const errorType = bounded(obj.type) ?? bounded(get(error, "type"));
    const errorCode =
      bounded(obj.code) ??
      bounded(get(obj, "details", "error_code")) ??
      bounded(get(error, "code"));
    if (errorType !== undefined) span.setAttribute(ERROR_TYPE, errorType);
    if (errorCode !== undefined) span.setAttribute(ERROR_CODE, errorCode);
  } catch (err) {
    log.debug("Failed to record provider error details", err);
  }
}
