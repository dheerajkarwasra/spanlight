/**
 * Provider-agnostic plumbing shared by the OpenAI and Anthropic wrappers.
 *
 * Both provider SDKs return an `APIPromise`: a `Promise` subclass whose `responsePromise` field
 * settles with the HTTP response (or rejects with the provider's error) and whose
 * `parseResponse` field turns that response into data, lazily, when the caller awaits it or
 * calls `withResponse()`. Streams are a `Stream` whose events come from its `iterator` field.
 * The wrappers hook those fields on the objects the provider SDK returns, so the caller still
 * gets the provider's own promise and stream, with every helper on them intact.
 */

import { getClient } from "../client.js";
import { log, safely } from "../log.js";
import type { Span } from "../span.js";

export type Obj = Record<string, unknown>;

/** How one provider method is traced. */
export interface Endpoint {
  provider: string;
  /** The span name's first word, e.g. `chat` in `chat gpt-4o-mini`. */
  operation: string;
  requestAttributes?(body: Obj): Obj;
  recordResponse(span: Span, response: unknown): void;
  newAccumulator(): StreamAccumulator;
}

export interface StreamEnd {
  closedEarly?: boolean;
  error?: unknown;
}

/** Collects one stream's events and writes the result onto the span when it ends. */
export interface StreamAccumulator {
  onEvent(event: unknown, span: Span): void;
  finish(span: Span, end: StreamEnd): void;
}

const WRAPPED = Symbol.for("spanlight.wrapped");
const GEO_PATTERN = /^[a-z0-9_-]{1,32}$/;

/** Read a nested field, returning undefined when any step is missing. */
export function get(value: unknown, ...path: string[]): unknown {
  let current = value;
  for (const key of path) {
    if (current === null || typeof current !== "object") return undefined;
    current = (current as Obj)[key];
  }
  return current;
}

export function asInt(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : undefined;
}

export function asStr(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/** Replace `resource.create` with a traced version, at most once. */
export function patchCreate(resource: unknown, endpoint: Endpoint): void {
  const target = resource as { create?: unknown } | undefined;
  if (target === undefined) return;
  const original = target.create;
  if (typeof original !== "function" || (original as { [WRAPPED]?: boolean })[WRAPPED]) return;
  const create = function (this: unknown, ...args: unknown[]): unknown {
    const span = startLlmSpan(endpoint, args[0]);
    if (span === undefined) return original.apply(this, args);
    let result: unknown;
    try {
      result = original.apply(this, args);
    } catch (error) {
      finishWithError(span, error);
      throw error;
    }
    observeApiPromise(result, span, endpoint);
    return result;
  };
  Object.defineProperty(create, WRAPPED, { value: true });
  target.create = create;
}

function startLlmSpan(endpoint: Endpoint, body: unknown): Span | undefined {
  try {
    const client = getClient();
    if (!client.enabled || body === null || typeof body !== "object") return undefined;
    const request = body as Obj;
    const model = asStr(request.model);
    const span = client.startSpan(
      model ? `${endpoint.operation} ${model}` : endpoint.operation,
      "llm",
    );
    span.setModel(model, endpoint.provider);
    span.setInput(request);
    if (request.stream === true) span.setAttribute("request.stream", true);
    const modifier = pricingModifierUnknown(request);
    if (modifier !== undefined) span.setAttribute("usage.pricing_modifier_unknown", modifier);
    span.setAttributes(endpoint.requestAttributes?.(request) ?? {});
    return span;
  } catch (error) {
    log.debug("Failed to start an LLM span", error);
    return undefined;
  }
}

/**
 * Name the request option whose price Spanlight does not model, as the Python SDK does: data
 * residency (`inference_geo` other than `global`) wins over fast mode (`speed: "fast"`).
 */
function pricingModifierUnknown(request: Obj): string | undefined {
  const geo = asStr(request.inference_geo)?.trim().toLowerCase();
  if (geo && geo !== "global") return `inference_geo_${GEO_PATTERN.test(geo) ? geo : "other"}`;
  if (asStr(request.speed)?.trim().toLowerCase() === "fast") return "fast_mode";
  return undefined;
}

interface ApiPromiseFields {
  responsePromise?: unknown;
  parseResponse?: unknown;
}

function observeApiPromise(result: unknown, span: Span, endpoint: Endpoint): void {
  const promise = result as ApiPromiseFields | null;
  const parse = promise?.parseResponse;
  const response = promise?.responsePromise;
  if (promise === null || typeof parse !== "function" || !(response instanceof Promise)) {
    // An SDK version without these fields: the call runs untraced and its span is never sent.
    log.debug(`Unrecognised ${endpoint.provider} SDK result; the call is not traced`);
    return;
  }
  promise.responsePromise = response.then(undefined, (error: unknown) => {
    finishWithError(span, error);
    throw error;
  });
  promise.parseResponse = async function (this: unknown, ...args: unknown[]): Promise<unknown> {
    let data: unknown;
    try {
      data = await (parse as (...a: unknown[]) => unknown).apply(this, args);
    } catch (error) {
      finishWithError(span, error);
      throw error;
    }
    safely("record a provider response", () => {
      if (isStream(data)) {
        observeStream(data, span, endpoint.newAccumulator());
      } else {
        endpoint.recordResponse(span, data);
        span.end();
      }
    });
    return data;
  };
}

interface StreamFields {
  iterator: (this: unknown) => AsyncIterator<unknown>;
  controller: unknown;
}

function isStream(value: unknown): value is StreamFields {
  return (
    typeof (value as StreamFields | null)?.iterator === "function" &&
    "controller" in (value as object)
  );
}

/** Pass every event of `stream` through the accumulator; the span ends with the stream. */
function observeStream(stream: StreamFields, span: Span, accumulator: StreamAccumulator): void {
  const iterate = stream.iterator;
  stream.iterator = function (this: unknown): AsyncIterator<unknown> {
    return observeEvents(iterate.call(this), span, accumulator);
  };
}

async function* observeEvents(
  inner: AsyncIterator<unknown>,
  span: Span,
  accumulator: StreamAccumulator,
): AsyncGenerator {
  let exhausted = false;
  try {
    for await (const event of { [Symbol.asyncIterator]: () => inner }) {
      safely("observe a stream event", () => accumulator.onEvent(event, span));
      yield event;
    }
    exhausted = true;
  } catch (error) {
    safely("finish a failed stream", () => accumulator.finish(span, { error }));
    finishWithError(span, error);
    throw error;
  } finally {
    // Runs on exhaustion, and when the caller breaks out of the loop (closed early).
    if (!span.isEnded) {
      safely("finish a stream", () => accumulator.finish(span, { closedEarly: !exhausted }));
      span.end();
    }
  }
}

/** Record a provider error on `span` and end it. Never throws. */
export function finishWithError(span: Span, error: unknown): void {
  safely("record a provider error", () => {
    span.recordError(error);
    const status = asInt(get(error, "status"));
    if (status !== undefined) span.setAttribute("http.status_code", status);
    const requestId = asStr(get(error, "requestID"));
    if (requestId) span.setAttribute("provider.request_id", requestId);
    // A call that failed before any usage came back was priced at no rate, so it names no modifier.
    if (span.inputTokens === undefined) delete span.attributes["usage.pricing_modifier_unknown"];
    span.end();
  });
}

/** Why a stream has no final usage (`usage.unreported_reason`). */
export function unreportedReason(end: StreamEnd, otherwise: string): string {
  if (end.error !== undefined) return "stream_error";
  if (end.closedEarly) return "client_disconnected";
  return otherwise;
}
