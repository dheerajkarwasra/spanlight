import { describe, expect, it } from "vitest";

import { tokenize } from "@/lib/highlight";

import { snippetLines, splitTokensIntoLines } from "./snippet-lines";
import {
  describeAge,
  failedSpansLabel,
  formatAge,
  primaryModelLabel,
  tokenLabel,
  traceDisplayName,
} from "./trace-rows";

describe("formatAge", () => {
  const now = new Date("2026-10-08T12:00:00Z");

  it("uses the largest whole unit", () => {
    expect(formatAge("2026-10-08T11:59:48Z", now)).toBe("12s");
    expect(formatAge("2026-10-08T11:56:00Z", now)).toBe("4m");
    expect(formatAge("2026-10-08T09:00:00Z", now)).toBe("3h");
    expect(formatAge("2026-10-06T08:00:00Z", now)).toBe("2d");
  });

  it("never shows a negative age for clock skew", () => {
    expect(formatAge("2026-10-08T12:00:05Z", now)).toBe("0s");
  });

  it("returns null for an invalid timestamp", () => {
    expect(formatAge("soon", now)).toBeNull();
  });

  it("spells the age out for screen readers", () => {
    expect(describeAge("2026-10-08T11:59:00Z", now)).toBe("1 minute ago");
    expect(describeAge("2026-10-08T09:00:00Z", now)).toBe("3 hours ago");
  });
});

describe("trace row labels", () => {
  it("falls back to a short id when the trace has no name", () => {
    expect(traceDisplayName({ name: null, trace_id: "1a2b3c4d5e6f" })).toBe("Trace 1a2b3c4d");
    expect(traceDisplayName({ name: "answer_ticket", trace_id: "x" })).toBe("answer_ticket");
  });

  it("shows the first model and how many more", () => {
    expect(primaryModelLabel([])).toBeNull();
    expect(primaryModelLabel(["gpt-4.1-mini"])).toBe("gpt-4.1-mini");
    expect(primaryModelLabel(["a", "b", "c"])).toBe("a +2");
  });

  it("shows tokens only for traces that called a model", () => {
    expect(tokenLabel({ input_tokens: 1500, output_tokens: 340, models: ["m"] })).toBe("1,840 tok");
    expect(tokenLabel({ input_tokens: 0, output_tokens: 0, models: ["m"] })).toBe("0 tok");
    expect(tokenLabel({ input_tokens: 0, output_tokens: 0, models: [] })).toBeNull();
    // No call reported usage: unknown, not "0 tok".
    expect(tokenLabel({ input_tokens: null, output_tokens: null, models: ["m"] })).toBeNull();
  });

  it("counts failed spans", () => {
    expect(failedSpansLabel(0)).toBeNull();
    expect(failedSpansLabel(1)).toBe("1 failed span");
    expect(failedSpansLabel(2)).toBe("2 failed spans");
  });
});

describe("snippet lines", () => {
  it("splits tokens at line breaks and keeps blank lines", () => {
    const lines = splitTokensIntoLines(tokenize('a = "x"\n\nb', "python"));
    expect(lines).toHaveLength(3);
    expect(lines[1]).toEqual([]);
    expect(lines[0]?.map((token) => token.kind)).toEqual(["plain", "string"]);
  });

  it("prefixes one-line shell commands with a prompt and separates blocks", () => {
    const lines = snippetLines([
      { language: "shell", code: "pip install spanlight" },
      { language: "python", code: "import spanlight" },
    ]);
    expect(lines).toHaveLength(3);
    expect(lines[0]?.[0]).toEqual({ kind: "prompt", text: "$" });
    expect(lines[1]).toEqual([]);
    expect(lines[2]?.[0]).toEqual({ kind: "keyword", text: "import" });
  });
});
