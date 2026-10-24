import { describe, expect, it, vi } from "vitest";

import {
  classifyIngestResponse,
  newTestTraceIds,
  sendTestTrace,
  testTraceBody,
} from "./test-trace";

const IDS = { traceId: "a".repeat(32), spanId: "b".repeat(16) };

describe("newTestTraceIds", () => {
  it("returns 32 and 16 lowercase hex characters", () => {
    const { traceId, spanId } = newTestTraceIds();
    expect(traceId).toMatch(/^[0-9a-f]{32}$/);
    expect(spanId).toMatch(/^[0-9a-f]{16}$/);
  });

  it("never returns all zeros", () => {
    const spy = vi.spyOn(crypto, "getRandomValues");
    // The first draw of each id is all zeros, the second is not.
    spy.mockImplementationOnce((array) => array);
    spy.mockImplementationOnce((array) => {
      (array as Uint8Array).fill(1);
      return array;
    });
    spy.mockImplementationOnce((array) => array);
    spy.mockImplementationOnce((array) => {
      (array as Uint8Array).fill(2);
      return array;
    });
    const { traceId, spanId } = newTestTraceIds();
    spy.mockRestore();
    expect(traceId).toBe("01".repeat(16));
    expect(spanId).toBe("02".repeat(8));
  });
});

describe("testTraceBody", () => {
  it("builds the spec's span", () => {
    const now = new Date("2026-10-21T12:00:00.000Z");
    expect(testTraceBody(IDS, "staging", now)).toEqual({
      spans: [
        {
          trace_id: IDS.traceId,
          span_id: IDS.spanId,
          name: "Test trace from setup",
          kind: "chain",
          status: "ok",
          start_time: "2026-10-21T11:59:59.950Z",
          end_time: "2026-10-21T12:00:00.000Z",
          input: { message: "Sent from the setup page" },
          output: { message: "Spanlight received this span" },
          trace: {
            name: "Test trace from setup",
            environment: "staging",
            tags: ["spanlight:setup-test"],
          },
        },
      ],
    });
  });
});

describe("classifyIngestResponse", () => {
  it("accepts a 200 with no rejected spans", () => {
    expect(classifyIngestResponse(200, { accepted: 1, rejected: [] })).toEqual({ ok: true });
  });

  it("shows the reason of a rejected span", () => {
    expect(
      classifyIngestResponse(200, {
        accepted: 0,
        rejected: [{ index: 0, span_id: IDS.spanId, reason: "end_time is in the future" }],
      }),
    ).toEqual({ ok: false, message: "end_time is in the future", retry: true });
  });

  it.each([
    [401, "The key was rejected. Create a new key and try again.", false],
    [403, "This key can't write traces. Create a key with ingest access.", false],
    [429, "Too many requests. Try again in a few seconds.", true],
    [500, "Couldn't send the test trace. Check your connection and try again.", true],
    [503, "Couldn't send the test trace. Check your connection and try again.", true],
  ])("maps %i to its message", (status, message, retry) => {
    expect(classifyIngestResponse(status, null)).toEqual({ ok: false, message, retry });
  });

  it("treats a 200 without an accepted span as a failure", () => {
    expect(classifyIngestResponse(200, null)).toEqual({
      ok: false,
      message: "Couldn't send the test trace. Check your connection and try again.",
      retry: true,
    });
  });
});

describe("sendTestTrace", () => {
  const body = testTraceBody(IDS, "production", new Date("2026-10-21T12:00:00Z"));

  it("posts the body with the key as a bearer token and no cookies", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ accepted: 1, rejected: [] }), { status: 200 }),
      );
    await expect(sendTestTrace("spl_live_x_y", body, fetchImpl)).resolves.toEqual({ ok: true });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("/v1/traces");
    expect(init).toMatchObject({
      method: "POST",
      credentials: "omit",
      headers: { Authorization: "Bearer spl_live_x_y", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  });

  it("maps a network error to the retryable message", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(sendTestTrace("k", body, fetchImpl)).resolves.toEqual({
      ok: false,
      message: "Couldn't send the test trace. Check your connection and try again.",
      retry: true,
    });
  });
});
