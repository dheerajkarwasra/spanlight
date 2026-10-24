import { describe, expect, it } from "vitest";

import type { ReleaseStats } from "@/lib/api";

import { markerLines, markersInWindow, windowMarkers } from "./release-markers";

function release(name: string, firstSeenAt: string): ReleaseStats {
  return { release: name, first_seen_at: firstSeenAt } as ReleaseStats;
}

const FROM = "2026-10-16T00:00:00Z";
const TO = "2026-10-17T00:00:00Z";

describe("markersInWindow", () => {
  it("keeps releases first seen inside the window, newest first", () => {
    const markers = markersInWindow(
      [
        release("v1", "2026-10-15T23:59:00Z"),
        release("v2", "2026-10-16T03:10:00Z"),
        release("v3", "2026-10-16T09:00:00Z"),
        release("v4", "2026-10-17T00:00:01Z"),
      ],
      FROM,
      TO,
      "hour",
    );
    expect(markers).toEqual([
      { release: "v3", at: "2026-10-16T09:00:00Z" },
      { release: "v2", at: "2026-10-16T03:10:00Z" },
    ]);
  });

  it("keeps at most the 8 most recent", () => {
    const releases = Array.from({ length: 10 }, (_, index) =>
      release(`v${index}`, `2026-10-16T${String(index + 10)}:00:00Z`),
    );
    const markers = markersInWindow(releases, FROM, TO, "hour");
    expect(markers).toHaveLength(8);
    expect(markers[0]?.release).toBe("v9");
    expect(markers.at(-1)?.release).toBe("v2");
  });

  it("drops releases first seen in the window's first bucket: they were live before it", () => {
    const releases = [
      release("old", "2026-10-16T00:00:03Z"),
      release("old-too", "2026-10-16T00:59:59Z"),
      release("new", "2026-10-16T01:00:00Z"),
    ];
    expect(markersInWindow(releases, "2026-10-16T00:20:00Z", TO, "hour")).toEqual([
      { release: "new", at: "2026-10-16T01:00:00Z" },
    ]);
  });
});

describe("windowMarkers", () => {
  const releases = [release("v2", "2026-10-16T03:10:00Z")];

  it("draws the 7-day window's markers", () => {
    const week = { from: "2026-10-10T00:00:00Z", to: TO, durationMs: 7 * 86_400_000 };
    expect(windowMarkers(releases, week, "day")).toHaveLength(1);
  });

  it("draws none over 30 days, even with the last window's releases still held", () => {
    const longWindow = { from: "2026-07-19T00:00:00Z", to: TO, durationMs: 90 * 86_400_000 };
    expect(windowMarkers(releases, longWindow, "day")).toEqual([]);
  });

  it("draws none while the releases are unknown", () => {
    expect(
      windowMarkers(undefined, { from: FROM, to: TO, durationMs: 86_400_000 }, "hour"),
    ).toEqual([]);
  });
});

describe("markerLines", () => {
  const points = ["00", "01", "02"].map((hour) => ({ bucketStart: `2026-10-16T${hour}:00:00Z` }));

  it("puts each marker on its bucket and joins releases that share one", () => {
    expect(
      markerLines(
        [
          { release: "v3", at: "2026-10-16T02:30:00Z" },
          { release: "v2", at: "2026-10-16T00:40:00Z" },
          { release: "v1", at: "2026-10-16T00:05:00Z" },
          { release: "v0", at: "2026-10-16T05:00:00Z" },
        ],
        points,
        "hour",
      ),
    ).toEqual([
      { bucketStart: "2026-10-16T02:00:00Z", label: "v3" },
      { bucketStart: "2026-10-16T00:00:00Z", label: "v2, v1" },
    ]);
  });
});
