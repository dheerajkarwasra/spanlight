import { describe, expect, it } from "vitest";

import { RETRY_NO_KNOWN_COST_REASON, RETRY_NOT_COMPUTED_REASON } from "@/lib/retry-reasons";

import {
  formatRetries,
  hasUnknownRetries,
  retryBadgeText,
  retrySummary,
  retryCostColumn,
} from "./retry-format";

describe("formatRetries", () => {
  it("shows a trace stored before the retry columns as unknown, with the date it changed", () => {
    expect(formatRetries(null, null)).toEqual({
      count: "—",
      cost: "—",
      reason: "not computed for traces stored before 2026-10-15",
    });
    expect(RETRY_NOT_COMPUTED_REASON).toBe("not computed for traces stored before 2026-10-15");
  });

  it("keeps a known count but marks the cost unknown when a retried call is unpriced", () => {
    expect(formatRetries(3, null)).toEqual({
      count: "3",
      cost: "—",
      reason: "a retried call has no known cost",
    });
    expect(RETRY_NO_KNOWN_COST_REASON).toBe("a retried call has no known cost");
  });

  it("shows a known zero as 0 without a tooltip", () => {
    expect(formatRetries(0, "0")).toEqual({ count: "0", cost: "$0.00", reason: null });
  });

  it("formats a known count and cost", () => {
    expect(formatRetries(3, "0.0412")).toEqual({ count: "3", cost: "$0.0412", reason: null });
  });
});

describe("retrySummary", () => {
  it("reads as the trace header line", () => {
    expect(retrySummary(3, "0.0412")).toBe("3 retries · $0.0412 retry cost");
    expect(retrySummary(1, "0.0164")).toBe("1 retry · $0.0164 retry cost");
    expect(retrySummary(3, null)).toBe("3 retries · — retry cost");
    expect(retrySummary(null, null)).toBe("Retries — · retry cost —");
  });

  it("is null when the trace has no retries", () => {
    expect(retrySummary(0, "0")).toBeNull();
  });
});

describe("retryBadgeText", () => {
  it("carries the cost beside the count on the phone tile", () => {
    expect(retryBadgeText(3, "0.0291")).toBe("3 retries · $0.0291");
    expect(retryBadgeText(1, "0.0164")).toBe("1 retry · $0.0164");
    expect(retryBadgeText(3, null)).toBe("3 retries · —");
    expect(retryBadgeText(null, null)).toBe("Retries —");
    expect(retryBadgeText(0, "0")).toBeNull();
  });
});

describe("retryCostColumn", () => {
  it("shows the retry cost column while the retry-cost sort or the has-retries filter is on", () => {
    expect(retryCostColumn({})).toBe("hidden");
    expect(retryCostColumn({ sort: "started_at" })).toBe("hidden");
    expect(retryCostColumn({ sort: "retry_cost_usd" })).toBe("sorted");
    expect(retryCostColumn({ sort: "retry_cost_usd", has_retries: true })).toBe("sorted");
    expect(retryCostColumn({ has_retries: true })).toBe("shown");
    expect(retryCostColumn({ has_retries: false })).toBe("hidden");
  });
});

describe("hasUnknownRetries", () => {
  it("is true only when a trace shows a dash form", () => {
    expect(hasUnknownRetries([{ retry_calls: 0, retry_cost_usd: "0" }])).toBe(false);
    expect(hasUnknownRetries([{ retry_calls: 2, retry_cost_usd: "0.01" }])).toBe(false);
    expect(hasUnknownRetries([{ retry_calls: 2, retry_cost_usd: null }])).toBe(true);
    expect(hasUnknownRetries([{ retry_calls: null, retry_cost_usd: null }])).toBe(true);
  });
});
