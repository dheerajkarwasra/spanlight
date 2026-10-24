import { describe, expect, it } from "vitest";

import type { ReleaseStats } from "@/lib/api";

import { defaultPair } from "./default-pair";

function stats(release: string): ReleaseStats {
  return {
    release,
    first_seen_at: "2026-10-01T00:00:00Z",
    last_seen_at: "2026-10-02T00:00:00Z",
    traces: 1,
    llm_calls: 1,
    unpriced_calls: 0,
    error_rate: 0,
    p50_ms: 1,
    p95_ms: 1,
    cost_usd: "0.01",
    input_tokens: 1,
    output_tokens: 1,
  };
}

// The list comes newest first (`last_seen_at DESC`), so [0] is the latest release.
const THREE = [stats("v3"), stats("v2"), stats("v1")];

describe("defaultPair", () => {
  it.each([
    [
      "no URL pair: previous vs latest",
      THREE,
      undefined,
      undefined,
      { a: "v2", b: "v3", derived: true },
    ],
    ["both in the URL win", THREE, "v1", "v3", { a: "v1", b: "v3", derived: false }],
    [
      "one in the URL wins as is",
      THREE,
      "v1",
      undefined,
      { a: "v1", b: undefined, derived: false },
    ],
    [
      "one release: nothing to derive",
      [stats("v1")],
      undefined,
      undefined,
      { a: undefined, b: undefined, derived: false },
    ],
    [
      "no releases: nothing to derive",
      [],
      undefined,
      undefined,
      { a: undefined, b: undefined, derived: false },
    ],
  ])("%s", (_name, list, a, b, expected) => {
    expect(defaultPair(list, a, b)).toEqual(expected);
  });
});
