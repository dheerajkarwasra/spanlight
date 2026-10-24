/** The fields of one native ingest span that the setup test trace sets. */
export interface SpanIn {
  trace_id: string;
  span_id: string;
  name: string;
  kind: "chain";
  status: "ok";
  start_time: string;
  end_time: string;
  input: { message: string };
  output: { message: string };
  trace: { name: string; environment: string; tags: string[] };
}

export interface TestTraceIds {
  traceId: string;
  spanId: string;
}

export type IngestResult = { ok: true } | { ok: false; message: string; retry: boolean };

const NAME = "Test trace from setup";
const FAILED = "Couldn't send the test trace. Check your connection and try again.";
// A hung request would leave the button spinning; the span upserts, so a retry is safe.
const TIMEOUT_MS = 10_000;

/** Random lowercase hex of `bytes` bytes, never all zeros (ingest rejects a zero id). */
function randomHex(bytes: number): string {
  const array = new Uint8Array(bytes);
  do {
    crypto.getRandomValues(array);
  } while (array.every((byte) => byte === 0));
  return Array.from(array, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Ids for the test trace. Generate them once per key and reuse them on every retry, so a
 * repeat upserts the same span instead of adding a second trace.
 */
export function newTestTraceIds(): TestTraceIds {
  return { traceId: randomHex(16), spanId: randomHex(8) };
}

/** One 50 ms chain span with no model or usage, so its cost stays unknown. */
export function testTraceBody(
  ids: TestTraceIds,
  environment: string,
  now: Date,
): { spans: [SpanIn] } {
  return {
    spans: [
      {
        trace_id: ids.traceId,
        span_id: ids.spanId,
        name: NAME,
        kind: "chain",
        status: "ok",
        start_time: new Date(now.getTime() - 50).toISOString(),
        end_time: now.toISOString(),
        input: { message: "Sent from the setup page" },
        output: { message: "Spanlight received this span" },
        trace: { name: NAME, environment, tags: ["spanlight:setup-test"] },
      },
    ],
  };
}

/** Maps an ingest answer to success or the message to show. 401 and 403 need a new key. */
export function classifyIngestResponse(status: number, body: unknown): IngestResult {
  if (status === 401) {
    return {
      ok: false,
      message: "The key was rejected. Create a new key and try again.",
      retry: false,
    };
  }
  if (status === 403) {
    return {
      ok: false,
      message: "This key can't write traces. Create a key with ingest access.",
      retry: false,
    };
  }
  if (status === 429) {
    return { ok: false, message: "Too many requests. Try again in a few seconds.", retry: true };
  }
  if (status === 200 && body && typeof body === "object") {
    const { accepted, rejected } = body as { accepted?: unknown; rejected?: unknown };
    const reason = Array.isArray(rejected)
      ? (rejected[0] as { reason?: unknown } | undefined)?.reason
      : undefined;
    if (typeof reason === "string") {
      return { ok: false, message: reason, retry: true };
    }
    if (typeof accepted === "number" && accepted > 0) {
      return { ok: true };
    }
  }
  return { ok: false, message: FAILED, retry: true };
}

/**
 * Posts the test trace with a raw `fetch`: the shared api client would add the session's CSRF
 * header and redirect to sign-in on a 401. No cookies go with it; the key is the only credential.
 */
export async function sendTestTrace(
  apiKey: string,
  body: { spans: [SpanIn] },
  fetchImpl: typeof fetch = fetch,
): Promise<IngestResult> {
  try {
    const response = await fetchImpl("/v1/traces", {
      method: "POST",
      credentials: "omit",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const json: unknown = await response.json().catch(() => null);
    return classifyIngestResponse(response.status, json);
  } catch {
    return { ok: false, message: FAILED, retry: true };
  }
}
