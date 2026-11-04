import { describe, expect, it } from "vitest";

import { attemptsLabel, canRetry } from "./delivery-status";

// A render-failed email is `failed` and `retryable: false`: the log must not offer a Retry that
// can only answer 409, nor promise attempts that will never come.

describe("canRetry", () => {
  it("offers Retry only for a failed row the server would take back", () => {
    expect(canRetry({ status: "failed", retryable: true })).toBe(true);
    expect(canRetry({ status: "failed", retryable: false })).toBe(false);
    expect(canRetry({ status: "pending", retryable: false })).toBe(false);
    expect(canRetry({ status: "sent", retryable: false })).toBe(false);
  });
});

describe("attemptsLabel", () => {
  it("counts against the maximum while more attempts can come", () => {
    expect(attemptsLabel({ status: "pending", attempts: 3, retryable: false })).toBe("3 of 8");
    expect(attemptsLabel({ status: "failed", attempts: 8, retryable: true })).toBe("8 of 8");
  });

  it("shows only the attempts made for a failure nothing will retry", () => {
    expect(attemptsLabel({ status: "failed", attempts: 1, retryable: false })).toBe("1");
  });
});
