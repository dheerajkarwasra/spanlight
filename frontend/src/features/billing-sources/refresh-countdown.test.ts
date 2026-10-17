import { describe, expect, it } from "vitest";

import { DEFAULT_RETRY_SECONDS, retryAfterLabel, retryDeadline } from "./refresh-countdown";

const NOW = Date.parse("2026-10-16T12:00:00Z");

describe("retryDeadline", () => {
  it("is now plus the server's Retry-After", () => {
    expect(retryDeadline(252, NOW)).toBe(NOW + 252_000);
  });

  it("falls back to the five-minute refresh interval when the header is missing", () => {
    expect(DEFAULT_RETRY_SECONDS).toBe(300);
    expect(retryDeadline(null, NOW)).toBe(NOW + 300_000);
  });
});

describe("retryAfterLabel", () => {
  it("counts down in minutes and zero-padded seconds", () => {
    expect(retryAfterLabel(NOW + 252_000, NOW)).toBe("4:12");
    expect(retryAfterLabel(NOW + 5_000, NOW)).toBe("0:05");
    expect(retryAfterLabel(NOW + 600_000, NOW)).toBe("10:00");
  });

  it("rounds a partial second up, so it never shows 0:00 while still waiting", () => {
    expect(retryAfterLabel(NOW + 200, NOW)).toBe("0:01");
  });

  it("is null once the wait is over", () => {
    expect(retryAfterLabel(NOW, NOW)).toBeNull();
    expect(retryAfterLabel(NOW - 1_000, NOW)).toBeNull();
  });
});
