import { describe, expect, it } from "vitest";

import type { IngestQuota } from "@/lib/api";

import {
  EXHAUSTED_TEXT,
  NO_QUOTA_REASON,
  formatBytes,
  quotaRows,
  rateLine,
  sourceLabel,
} from "./ingest-quota-format";

function quota(overrides: Partial<IngestQuota["limits"]> = {}, today = {}): IngestQuota {
  return {
    limits: {
      spans_per_second: { value: 1000, source: "default" },
      spans_per_day: { value: 1_000_000, source: "project" },
      bytes_per_day: { value: 5 * 1024 ** 3, source: "default" },
      ...overrides,
    },
    today: {
      day: "2026-11-02",
      spans: 48_213,
      bytes: 221_884_113,
      resets_at: "2026-11-03T00:00:00Z",
      ...today,
    },
    rejected_days: [],
  };
}

describe("quotaRows", () => {
  it("shows used of limit with a meter ratio and the limit's source", () => {
    const [spans, bytes] = quotaRows(quota());

    expect(spans).toEqual({
      label: "Spans today",
      state: "under",
      text: "48,213 of 1,000,000 spans",
      ratio: 0.048213,
      source: "Set for this project",
    });
    expect(bytes.text).toBe("211.6 MiB of 5 GiB");
    expect(bytes.state).toBe("under");
    expect(bytes.source).toBe("Instance default");
  });

  it("is exhausted at the limit, with the meter full", () => {
    const [spans] = quotaRows(quota({}, { spans: 1_000_000 }));

    expect(spans.state).toBe("exhausted");
    expect(spans.ratio).toBe(1);
    expect(EXHAUSTED_TEXT).toBe("Quota used up; new spans are refused until 00:00 UTC.");
  });

  it("is unlimited with no text and no meter when the limit is null", () => {
    const [spans] = quotaRows(quota({ spans_per_day: { value: null, source: "default" } }));

    expect(spans).toEqual({
      label: "Spans today",
      state: "unlimited",
      text: null,
      ratio: null,
      source: "Instance default",
    });
    expect(NO_QUOTA_REASON).toBe("No daily quota on this instance");
  });
});

describe("formatBytes", () => {
  it("picks the largest binary unit and trims the decimal", () => {
    expect(formatBytes(5 * 1024 ** 3)).toBe("5 GiB");
    expect(formatBytes(221_884_113)).toBe("211.6 MiB");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(512)).toBe("512 bytes");
  });
});

describe("rateLine", () => {
  it("names the sustained rate, or nothing when unlimited", () => {
    expect(rateLine(1000)).toBe("1,000 spans per second");
    expect(rateLine(null)).toBeNull();
  });
});

describe("sourceLabel", () => {
  it("tells an override from the instance default", () => {
    expect(sourceLabel("project")).toBe("Set for this project");
    expect(sourceLabel("default")).toBe("Instance default");
  });
});
