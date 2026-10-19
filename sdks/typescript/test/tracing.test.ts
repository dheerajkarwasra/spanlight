import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import {
  flush,
  getCurrentTraceId,
  init,
  observe,
  updateTrace,
  VERSION,
  withSpan,
} from "../src/index.js";
import { stripTrailingSlashes } from "../src/client.js";
import { toJsonable } from "../src/serialize.js";
import { normalizeTier } from "../src/tiers.js";
import { captureSpanlight, type Json } from "./support.js";

describe("observe and withSpan", () => {
  it("nests spans across awaits and puts trace fields on every span", async () => {
    const capture = captureSpanlight({ environment: "production", release: "2026.10.19" });
    const search = observe((query: string) => [`doc for ${query}`], {
      name: "search_docs",
      kind: "retrieval",
    });
    const handle = observe(
      async (question: string) => {
        updateTrace({ userId: "u_42", sessionId: "chat-123", tags: ["beta"] });
        const docs = search(question);
        await Promise.resolve();
        return withSpan("format-answer", (span) => {
          span.setOutput(docs[0]);
          return `From our docs: ${docs[0] ?? ""}`;
        });
      },
      { name: "support-request" },
    );

    expect(await handle("refunds")).toBe("From our docs: doc for refunds");
    await flush();

    const spans = capture.spans();
    const byName = Object.fromEntries(spans.map((span) => [span.name, span])) as Record<
      string,
      Json
    >;
    const root = byName["support-request"];
    expect(root).toMatchObject({ parent_span_id: null, input: ["refunds"] });
    expect(byName.search_docs).toMatchObject({
      kind: "retrieval",
      parent_span_id: root?.span_id,
      output: ["doc for refunds"],
    });
    expect(byName["format-answer"]).toMatchObject({ parent_span_id: root?.span_id });
    for (const span of spans) {
      expect(span.trace_id).toBe(root?.trace_id);
      expect(span.trace).toEqual({
        name: "support-request",
        environment: "production",
        release: "2026.10.19",
        user_id: "u_42",
        session_id: "chat-123",
        tags: ["beta"],
      });
      expect(span.start_time).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/);
    }
  });

  it("records an error and re-throws the same object", async () => {
    const capture = captureSpanlight();
    const failure = new TypeError("bad input");
    const run = observe(() => Promise.reject(failure));

    await expect(run()).rejects.toBe(failure);
    expect(() =>
      withSpan("sync", () => {
        throw failure;
      }),
    ).toThrow(failure);
    await flush();

    for (const span of capture.spans()) {
      expect(span).toMatchObject({ status: "error", status_message: "bad input" });
      expect(span.attributes).toMatchObject({ "error.type": "TypeError" });
    }
  });

  it("exposes the trace id inside a span only", () => {
    captureSpanlight();
    expect(getCurrentTraceId()).toBeUndefined();
    const inside = withSpan("work", () => getCurrentTraceId());
    expect(inside).toMatch(/^[0-9a-f]{32}$/);
  });

  it("runs as a no-op without an API key", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const fetchSpy = vi.fn();
    init({ apiKey: "", fetch: fetchSpy });
    const add = observe((a: number, b: number) => a + b);

    expect(add(2, 3)).toBe(5);
    expect(await flush()).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });
});

describe("host", () => {
  it("strips trailing slashes in linear time", () => {
    expect(stripTrailingSlashes("http://h:8000///")).toBe("http://h:8000");
    expect(stripTrailingSlashes("http://h:8000")).toBe("http://h:8000");
    expect(stripTrailingSlashes("///")).toBe("");
    expect(stripTrailingSlashes(`${"/".repeat(100_000)}x`)).toHaveLength(100_001);
  });
});

describe("serialization", () => {
  it("handles cycles, odd values and marks truncation", () => {
    const node: Record<string, unknown> = { name: "root", when: new Date(0), big: 10n };
    node.self = node;
    expect(toJsonable(node)).toEqual({
      value: { name: "root", when: "1970-01-01T00:00:00.000Z", big: "10", self: "[circular]" },
      cut: false,
    });
    const long = toJsonable("x".repeat(40_000));
    expect(long.cut).toBe(true);
    expect((long.value as string).endsWith("...[truncated]")).toBe(true);
  });
});

describe("shared fixtures", () => {
  it("normalizes service tiers like the server", () => {
    const url = new URL("../../fixtures/service-tier-cases.json", import.meta.url);
    const cases = JSON.parse(readFileSync(url, "utf8")) as {
      provider: string;
      raw: string;
      expected: string | null;
    }[];
    for (const { provider, raw, expected } of cases) {
      expect(normalizeTier(provider, raw) ?? null, `${provider} ${raw}`).toBe(expected);
    }
    expect(normalizeTier("openai", "constructor")).toBeUndefined();
  });

  it("keeps VERSION equal to package.json", () => {
    const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")) as {
      version: string;
    };
    expect(VERSION).toBe(pkg.version);
  });
});
