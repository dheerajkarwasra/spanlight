import { describe, expect, it } from "vitest";

import { ApiError, parseRetryAfter } from "./errors";

const NOW = Date.parse("2026-10-16T12:00:00Z");

describe("parseRetryAfter", () => {
  it("reads a delay in seconds", () => {
    expect(parseRetryAfter("252", NOW)).toBe(252);
  });

  it("reads an HTTP date as the seconds left until it, rounded up", () => {
    expect(parseRetryAfter("Fri, 16 Oct 2026 12:04:12 GMT", NOW)).toBe(252);
  });

  it("is null without a usable header", () => {
    expect(parseRetryAfter(null, NOW)).toBeNull();
    expect(parseRetryAfter("soon", NOW)).toBeNull();
    expect(parseRetryAfter("-5", NOW)).toBeNull();
  });
});

describe("ApiError", () => {
  it("carries the Retry-After delay it was given, and null otherwise", () => {
    expect(new ApiError(429, { code: "RATE_LIMITED" }, 252).retryAfterSeconds).toBe(252);
    expect(new ApiError(409, { code: "CONFLICT" }).retryAfterSeconds).toBeNull();
  });
});
