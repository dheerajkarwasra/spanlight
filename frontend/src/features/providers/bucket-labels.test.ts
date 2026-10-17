import { describe, expect, it } from "vitest";

import { bucketRange, bucketTick, utcClock, utcMoment } from "./bucket-labels";

describe("bucket labels", () => {
  it("labels hourly buckets in UTC", () => {
    expect(bucketTick("2026-10-16T01:00:00Z", "hour")).toBe("01:00");
    expect(bucketRange("2026-10-16T01:00:00Z", "hour")).toBe("Oct 16 · 01:00 – 02:00");
    expect(bucketRange("2026-10-16T23:00:00Z", "hour")).toBe("Oct 16 · 23:00 – 00:00");
  });

  it("labels daily buckets by their UTC day", () => {
    expect(bucketTick("2026-10-16T00:00:00Z", "day")).toBe("Oct 16");
    expect(bucketRange("2026-10-16T00:00:00Z", "day")).toBe("Oct 16");
  });

  it("formats a UTC moment and refuses garbage", () => {
    expect(utcMoment("2026-10-16T05:30:00Z")).toBe("Oct 16, 05:30");
    expect(utcMoment("nope")).toBeNull();
    expect(bucketTick("nope", "hour")).toBe("");
    expect(utcClock("2026-10-16T05:27:41Z")).toBe("05:27:41");
    expect(utcClock("nope")).toBeNull();
  });
});
