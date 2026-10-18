import { describe, expect, it } from "vitest";

import type { ModelMetrics, ProviderIncident } from "@/lib/api";
import type { Band } from "@/lib/incident-bands";

import type { ChartPoint } from "./chart-data";
import {
  bandSpans,
  incidentsAt,
  incidentSummary,
  overviewBands,
  usedProviders,
} from "./incident-overlay";

function incident(overrides: Partial<ProviderIncident>): ProviderIncident {
  return {
    id: "inc_1",
    provider: "openai",
    title: "Elevated error rates across the API",
    impact: "critical",
    status: "resolved",
    api_affected: true,
    components: ["API"],
    started_at: "2026-10-16T08:00:00Z",
    resolved_at: "2026-10-16T10:00:00Z",
    url: "https://status.openai.com/incidents/abc",
    latest_update: null,
    ...overrides,
  };
}

function model(provider: string | null): ModelMetrics {
  return {
    provider,
    model: "m",
    calls: 1,
    errors: 0,
    p50_ms: null,
    p95_ms: null,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: null,
    approximate: false,
  };
}

const WINDOW = { start: new Date("2026-10-16T00:00:00Z"), end: new Date("2026-10-16T12:00:00Z") };

function point(bucketStart: string): ChartPoint {
  return {
    bucketStart,
    llmCalls: 1,
    errors: 0,
    succeeded: 1,
    partial: false,
    p95Ms: null,
    costUsd: null,
    tokens: 0,
  };
}

const POINTS = ["00", "01", "02", "03"].map((hour) => point(`2026-10-16T${hour}:00:00Z`));

function band(start: string, end: string, title = "A", critical = false): Band {
  return { start: new Date(start), end: new Date(end), title, critical };
}

describe("usedProviders", () => {
  it("lowercases the providers the models report and skips unknown ones", () => {
    expect(usedProviders([model("OpenAI"), model(null), model("anthropic")])).toEqual(
      new Set(["openai", "anthropic"]),
    );
  });
});

describe("overviewBands", () => {
  it("keeps API-affected incidents of the providers the project used, labelled", () => {
    const bands = overviewBands(
      [
        incident({}),
        incident({ id: "inc_2", provider: "google" }),
        incident({ id: "inc_3", api_affected: false }),
      ],
      new Set(["openai"]),
      WINDOW,
    );
    expect(bands).toEqual([
      {
        start: new Date("2026-10-16T08:00:00Z"),
        end: new Date("2026-10-16T10:00:00Z"),
        title: "OpenAI · Elevated error rates across the API (critical)",
        critical: true,
      },
    ]);
  });

  it("clips to the window and leaves the impact out when there is none", () => {
    const [only] = overviewBands(
      [incident({ impact: "", resolved_at: null, provider: "anthropic", title: "Slow" })],
      new Set(["anthropic"]),
      WINDOW,
    );
    expect(only?.title).toBe("Anthropic · Slow");
    expect(only?.end).toEqual(WINDOW.end);
  });
});

describe("bandSpans", () => {
  it("runs from the bucket holding the start to the first bucket at or after the end", () => {
    expect(
      bandSpans([band("2026-10-16T01:10:00Z", "2026-10-16T02:00:00Z")], POINTS, "hour"),
    ).toEqual([
      { key: "0", x1: POINTS[1]?.bucketStart, x2: POINTS[2]?.bucketStart, critical: false },
    ]);
  });

  it("covers the following bucket when the end falls inside one", () => {
    const [span] = bandSpans(
      [band("2026-10-16T01:10:00Z", "2026-10-16T01:20:00Z", "A", true)],
      POINTS,
      "hour",
    );
    expect([span?.x1, span?.x2, span?.critical]).toEqual([
      POINTS[1]?.bucketStart,
      POINTS[2]?.bucketStart,
      true,
    ]);
  });

  it("stops at the last bucket and skips a band outside every bucket", () => {
    const spans = bandSpans(
      [
        band("2026-10-16T02:30:00Z", "2026-10-16T09:00:00Z"),
        band("2026-10-15T20:00:00Z", "2026-10-15T21:00:00Z"),
      ],
      POINTS,
      "hour",
    );
    expect(spans).toEqual([
      { key: "0", x1: POINTS[2]?.bucketStart, x2: POINTS[3]?.bucketStart, critical: false },
    ]);
  });

  it("widens a band in the newest bucket back to the previous point, hourly and daily", () => {
    const [hourly] = bandSpans(
      [band("2026-10-16T03:10:00Z", "2026-10-16T03:40:00Z")],
      POINTS,
      "hour",
    );
    expect([hourly?.x1, hourly?.x2]).toEqual([POINTS[2]?.bucketStart, POINTS[3]?.bucketStart]);

    const days = ["14", "15", "16"].map((day) => point(`2026-10-${day}T00:00:00Z`));
    const [daily] = bandSpans([band("2026-10-16T08:00:00Z", "2026-10-16T12:00:00Z")], days, "day");
    expect([daily?.x1, daily?.x2]).toEqual([days[1]?.bucketStart, days[2]?.bucketStart]);
  });

  it("keeps a single bucket's band at that point when there is no earlier one", () => {
    const [only] = bandSpans(
      [band("2026-10-16T00:10:00Z", "2026-10-16T00:20:00Z")],
      POINTS.slice(0, 1),
      "hour",
    );
    expect([only?.x1, only?.x2]).toEqual([POINTS[0]?.bucketStart, POINTS[0]?.bucketStart]);
  });

  it("draws nothing without points", () => {
    expect(bandSpans([band("2026-10-16T01:00:00Z", "2026-10-16T02:00:00Z")], [], "hour")).toEqual(
      [],
    );
  });
});

describe("incidentsAt", () => {
  const bands = [
    band("2026-10-16T01:30:00Z", "2026-10-16T02:00:00Z", "A"),
    band("2026-10-16T02:00:00Z", "2026-10-16T03:00:00Z", "B"),
  ];

  it("lists the titles of the incidents overlapping a bucket", () => {
    expect(incidentsAt(bands, "2026-10-16T01:00:00Z", "hour")).toEqual(["A"]);
    expect(incidentsAt(bands, "2026-10-16T02:00:00Z", "hour")).toEqual(["B"]);
    expect(incidentsAt(bands, "2026-10-16T03:00:00Z", "hour")).toEqual([]);
  });

  it("uses a day for daily buckets", () => {
    expect(incidentsAt(bands, "2026-10-16T00:00:00Z", "day")).toEqual(["A", "B"]);
  });
});

describe("incidentSummary", () => {
  it("names the count and the titles once each", () => {
    expect(
      incidentSummary([
        band("2026-10-16T01:00:00Z", "2026-10-16T02:00:00Z", "A"),
        band("2026-10-16T05:00:00Z", "2026-10-16T06:00:00Z", "A"),
        band("2026-10-16T07:00:00Z", "2026-10-16T08:00:00Z", "B"),
      ]),
    ).toBe("2 provider incidents: A; B");
    expect(incidentSummary([band("2026-10-16T01:00:00Z", "2026-10-16T02:00:00Z", "A")])).toBe(
      "1 provider incident: A",
    );
  });

  it("is empty with no bands", () => {
    expect(incidentSummary([])).toBe("");
  });
});
