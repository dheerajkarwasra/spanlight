/**
 * Test doubles: a `fetch` that plays a provider API for the real `openai` and
 * `@anthropic-ai/sdk` clients, and a `fetch` that captures what the SDK sends to Spanlight.
 * The provider payloads mirror real API responses (field names, nesting, SSE framing), as the
 * Python SDK's `tests/provider_mocks.py` does.
 */

import { gunzipSync } from "node:zlib";

import { init } from "../src/index.js";
import type { SpanlightOptions } from "../src/index.js";

export type Json = Record<string, unknown>;
type Respond = (body: Json) => Response;

/** A provider `fetch` that answers every request with `respond` and records the requests. */
export function mockProvider(respond: Respond): { fetch: typeof fetch; requests: Json[] } {
  const requests: Json[] = [];
  const fetchMock = (_input: string | URL | Request, options?: RequestInit): Promise<Response> => {
    const body = JSON.parse((options?.body as string | undefined) ?? "{}") as Json;
    requests.push(body);
    return Promise.resolve(respond(body));
  };
  return { fetch: fetchMock, requests };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "request-id": "req_mock_123",
      "x-request-id": "req_mock_123",
    },
  });
}

export type SseEvent = [name: string | null, data: unknown];

export function sseResponse(events: SseEvent[]): Response {
  const body = events
    .map(([name, data]) => {
      const payload = typeof data === "string" ? data : JSON.stringify(data);
      return `${name === null ? "" : `event: ${name}\n`}data: ${payload}\n\n`;
    })
    .join("");
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

/** `init()` the SDK against a capturing `fetch`; `spans()` returns everything sent so far. */
export function captureSpanlight(options: SpanlightOptions = {}): {
  requests: { headers: Headers; body: Json }[];
  spans: () => Json[];
} {
  const requests: { headers: Headers; body: Json }[] = [];
  const fetchMock = (_input: string | URL | Request, request?: RequestInit): Promise<Response> => {
    const headers = new Headers(request?.headers);
    const raw = request?.body as Buffer | string;
    const text =
      headers.get("content-encoding") === "gzip" ? gunzipSync(raw).toString() : String(raw);
    const body = JSON.parse(text) as Json;
    requests.push({ headers, body });
    const accepted = (body.spans as unknown[]).length;
    return Promise.resolve(Response.json({ accepted, rejected: [] }, { status: 202 }));
  };
  init({ apiKey: "spl_live_test", host: "http://spanlight.test", fetch: fetchMock, ...options });
  return {
    requests,
    spans: () => requests.flatMap((request) => request.body.spans as Json[]),
  };
}

/** Collect every event of an async iterable. */
export async function drain(iterable: AsyncIterable<unknown>): Promise<unknown[]> {
  const events: unknown[] = [];
  for await (const event of iterable) events.push(event);
  return events;
}
