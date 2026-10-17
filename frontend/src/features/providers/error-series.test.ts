import { describe, expect, it } from "vitest";

import type { ErrorBucket, RateLimitKindCount, StatusSource } from "@/lib/api";

import { dominantKind, presentClasses, staleFeed, toStackedPoints } from "./error-series";

function bucket(start: string, byClass: ErrorBucket["by_class"]): ErrorBucket {
  return { bucket_start: start, by_class: byClass, approximate: false };
}

function kind(name: RateLimitKindCount["kind"], count: number): RateLimitKindCount {
  return { kind: name, count, with_retry_after: 0 };
}

function source(overrides: Partial<StatusSource>): StatusSource {
  return {
    provider: "anthropic",
    last_success_at: "2026-10-16T10:00:00Z",
    last_polled_at: "2026-10-16T10:00:00Z",
    last_outcome: "fetched",
    consecutive_failures: 0,
    ...overrides,
  };
}

describe("toStackedPoints", () => {
  const buckets = [
    bucket("2026-10-16T00:00:00Z", { timeout: 1, rate_limit: 3 }),
    bucket("2026-10-16T01:00:00Z", {}),
    bucket("2026-10-16T02:00:00Z", { gateway: 2 }),
  ];

  it("keeps every bucket in order with its total", () => {
    const points = toStackedPoints(buckets);
    expect(points.map((point) => [point.bucketStart, point.total])).toEqual([
      ["2026-10-16T00:00:00Z", 4],
      ["2026-10-16T01:00:00Z", 0],
      ["2026-10-16T02:00:00Z", 2],
    ]);
  });

  it("drops classes absent from every bucket and keeps zeros inside a bucket", () => {
    const points = toStackedPoints(buckets);
    expect(points[0]?.counts).toEqual({ rate_limit: 3, timeout: 1, gateway: 0 });
    expect(points[1]?.counts).toEqual({ rate_limit: 0, timeout: 0, gateway: 0 });
    expect(points[2]?.counts).toEqual({ rate_limit: 0, timeout: 0, gateway: 2 });
  });

  it("lists the present classes in the fixed order, whatever order the API used", () => {
    expect(presentClasses(toStackedPoints(buckets))).toEqual(["rate_limit", "timeout", "gateway"]);
  });

  it("carries the approximate flag", () => {
    const points = toStackedPoints([
      { bucket_start: "2026-10-16T00:00:00Z", by_class: {}, approximate: true },
    ]);
    expect(points[0]?.approximate).toBe(true);
  });

  it("returns nothing for no buckets", () => {
    expect(toStackedPoints([])).toEqual([]);
    expect(presentClasses([])).toEqual([]);
  });
});

describe("dominantKind", () => {
  it("is the largest provider 429 kind", () => {
    expect(dominantKind([kind("requests", 31), kind("tokens", 92), kind("unknown", 7)])).toBe(
      "tokens",
    );
  });

  it("ignores overloaded 5xx and the gateway's own key limit", () => {
    expect(dominantKind([kind("overloaded", 300), kind("gateway", 200), kind("requests", 2)])).toBe(
      "requests",
    );
  });

  it("breaks a tie with the first kind in kind order", () => {
    expect(dominantKind([kind("unknown", 5), kind("tokens", 5), kind("requests", 5)])).toBe(
      "requests",
    );
    expect(dominantKind([kind("acceleration", 4), kind("spend_cap", 4)])).toBe("spend_cap");
  });

  it("is null without a provider 429", () => {
    expect(dominantKind([])).toBeNull();
    expect(dominantKind([kind("overloaded", 3)])).toBeNull();
    expect(dominantKind([kind("tokens", 0)])).toBeNull();
  });
});

describe("staleFeed", () => {
  const now = new Date("2026-10-16T10:40:00Z");

  it("is fresh 29 minutes after the last success", () => {
    expect(staleFeed(source({ last_success_at: "2026-10-16T10:11:00Z" }), now)).toBe(false);
  });

  it("is stale 31 minutes after the last success", () => {
    expect(staleFeed(source({ last_success_at: "2026-10-16T10:09:00Z" }), now)).toBe(true);
  });

  it("is stale after a failed poll, however recent the last success", () => {
    expect(
      staleFeed(source({ last_success_at: "2026-10-16T10:39:00Z", consecutive_failures: 1 }), now),
    ).toBe(true);
  });

  it("is stale when the feed never answered", () => {
    expect(staleFeed(source({ last_success_at: null }), now)).toBe(true);
  });
});
