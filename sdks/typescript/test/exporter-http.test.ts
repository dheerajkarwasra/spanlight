import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { describe, expect, it, vi } from "vitest";
import { Exporter, parseRetryAfter, type ExporterOptions } from "../src/exporter.js";

type HttpReply = string | { status: number; headers: Record<string, string>; body: string };

async function ingest(
  replies: HttpReply[],
  run: (url: string, requests: unknown[]) => Promise<void>,
) {
  const requests: unknown[] = [];
  const server = createServer((request, response) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => {
      body += chunk;
    });
    request.on("end", () => {
      requests.push(JSON.parse(body));
      const reply = replies[Math.min(requests.length - 1, replies.length - 1)] ?? "";
      if (typeof reply === "string") response.end(reply);
      else {
        response.writeHead(reply.status, reply.headers);
        response.end(reply.body);
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await run(`http://127.0.0.1:${(server.address() as AddressInfo).port}`, requests);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
}

function exporter(url: string, options: Partial<ExporterOptions> = {}) {
  return new Exporter({
    url,
    apiKey: "secret-key",
    batchSize: 100,
    flushIntervalMs: 60_000,
    maxQueue: 100,
    gzip: false,
    timeoutMs: 1000,
    maxRetries: 1,
    fetch,
    sleep: async () => {},
    jitter: () => 0,
    ...options,
  });
}

const valid = JSON.stringify({ accepted: 2, rejected: [] });
const invalid = [
  "<html>secret</html>",
  '{"accepted":',
  "{}",
  "null",
  { accepted: 1, rejected: [] },
  { accepted: true, rejected: [{ index: 0 }] },
  { accepted: 0, rejected: [{ index: 0 }, { index: 0 }] },
  { accepted: 1, rejected: [{ index: 2 }] },
  { accepted: 1, rejected: [{ index: true }] },
  { accepted: 1, rejected: [{ index: -1 }] },
  { accepted: 1, rejected: [null] },
  { accepted: 1, rejected: [{ index: 0.5 }] },
  { accepted: -1, rejected: [] },
  { accepted: 2, rejected: 0 },
].map((reply) => (typeof reply === "string" ? reply : JSON.stringify(reply)));

describe("real HTTP acknowledgments", () => {
  it.each(invalid)("retries ambiguity with identical payload: %s", async (reply) => {
    await ingest([reply, valid], async (url, requests) => {
      const delivery = exporter(url);
      delivery.export({ span_id: "a" });
      delivery.export({ span_id: "b" });
      await delivery.flush();
      expect(requests).toHaveLength(2);
      expect(requests[0]).toEqual(requests[1]);
      expect(delivery.sentCount).toBe(2);
      expect(delivery.failedCount).toBe(0);
      await delivery.shutdown(100);
    });
  });
  it("counts exhausted ambiguity as loss", async () => {
    await ingest(["<html>secret</html>"], async (url, requests) => {
      const delivery = exporter(url);
      delivery.export({ span_id: "a" });
      await delivery.flush();
      expect(requests).toHaveLength(2);
      expect(delivery.sentCount).toBe(0);
      expect(delivery.failedCount).toBe(1);
      await delivery.shutdown(100);
    });
  });
});

it("rejects a Retry-After whose milliseconds overflow", () => {
  expect(parseRetryAfter("1e308")).toBeUndefined();
});

it.each(["120", "2147484"])("default wait never retries before the minimum %s", async (header) => {
  await ingest(
    [{ status: 503, headers: { "retry-after": header }, body: "" }, valid],
    async (url, requests) => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
      const attempts: number[] = [];
      const delivery = exporter(url, {
        sleep: undefined,
        fetch: (...args) => {
          attempts.push(performance.now());
          return fetch(...args);
        },
      });
      try {
        delivery.export({ span_id: "a" });
        delivery.export({ span_id: "b" });
        const flushed = delivery.flush();
        await vi.waitFor(() => expect(vi.getTimerCount()).toBeGreaterThan(0));
        // Wait for the first HTTP response to enter backoff rather than a transport timeout.
        await vi.waitFor(() => expect(requests).toHaveLength(1));
        await new Promise<void>((resolve) => setImmediate(resolve));
        await new Promise<void>((resolve) => setImmediate(resolve));
        const minimum = Number(header) * 1000;
        await vi.advanceTimersByTimeAsync(Math.max(0, minimum - 1 - performance.now()));
        expect(attempts).toHaveLength(1);
        expect(requests).toHaveLength(1);
        await vi.advanceTimersByTimeAsync(minimum + 100 - performance.now());
        await vi.waitFor(() => expect(requests).toHaveLength(2));
        await flushed;
        expect(delivery.sentCount).toBe(2);
      } finally {
        await delivery.shutdown(0);
        vi.useRealTimers();
      }
    },
  );
});

it.each(["1e20", "9".repeat(400)])(
  "shutdown cancels a very long default server wait without another request: %s",
  async (header) => {
    await ingest(
      [{ status: 503, headers: { "retry-after": header }, body: "" }],
      async (url, requests) => {
        const delivery = exporter(url, { sleep: undefined });
        delivery.export({ span_id: "a" });
        const flushed = delivery.flush();
        await vi.waitFor(() => expect(requests).toHaveLength(1));
        await new Promise((resolve) => setTimeout(resolve, 20));
        await delivery.shutdown(10);
        await flushed;
        expect(requests).toHaveLength(1);
        expect(delivery.sentCount).toBe(0);
        expect(delivery.failedCount).toBe(1);
      },
    );
  },
);

it("every partial acknowledgment conserves entries and preserves batch order", async () => {
  const replies: string[] = [];
  const cases: { rejected: number; accepted: number; spans: { span_id: string }[] }[] = [];
  for (let size = 1; size <= 5; size += 1) {
    for (let mask = 0; mask < 2 ** size; mask += 1) {
      const spans = Array.from({ length: size }, (_, index) => ({ span_id: String(index) }));
      const rejected = spans.flatMap((_, index) => (mask & (1 << index) ? [{ index }] : []));
      const accepted = size - rejected.length;
      cases.push({ rejected: rejected.length, accepted, spans });
      replies.push(JSON.stringify({ accepted, rejected: rejected.reverse() }));
    }
  }
  await ingest(replies, async (url, requests) => {
    const delivery = exporter(url);
    for (const testCase of cases) {
      const sent = delivery.sentCount;
      const failed = delivery.failedCount;
      for (const span of testCase.spans) delivery.export(span);
      await delivery.flush();
      expect(delivery.sentCount - sent).toBe(testCase.accepted);
      expect(delivery.failedCount - failed).toBe(testCase.rejected);
      expect(requests.at(-1)).toMatchObject({ spans: testCase.spans });
    }
    expect(requests).toHaveLength(cases.length);
    await delivery.shutdown(100);
  });
});

it("retains overflowing digit-only Retry-After as an indefinite cancellable minimum", () => {
  expect(parseRetryAfter("9".repeat(400))).toBe(Infinity);
});

it("logs exhausted ambiguity without response secrets or credentials", async () => {
  const warning = vi.spyOn(console, "warn");
  try {
    await ingest(["<html>private-response-content</html>"], async (url) => {
      const delivery = exporter(url);
      delivery.export({ span_id: "a", input: "private-span-content" });
      await delivery.flush();
      const text = warning.mock.calls.flat().join(" ");
      expect(text).toContain("ambiguous acknowledgment");
      expect(text).not.toContain("private-response-content");
      expect(text).not.toContain("private-span-content");
      expect(text).not.toContain("secret-key");
      await delivery.shutdown(100);
    });
  } finally {
    warning.mockRestore();
  }
});

it("untrusted rejection diagnostics cannot change confirmed counters or trigger a retry", async () => {
  const acknowledgment = {
    accepted: 1,
    rejected: [{ index: 1, span_id: { toString: null }, reason: { toString: null } }],
  };
  await ingest([JSON.stringify(acknowledgment)], async (url, requests) => {
    const delivery = exporter(url);
    delivery.export({ span_id: "a" });
    delivery.export({ span_id: "b" });
    await delivery.flush();
    expect(requests).toHaveLength(1);
    expect(delivery.sentCount).toBe(1);
    expect(delivery.failedCount).toBe(1);
    await delivery.shutdown(100);
  });
});
