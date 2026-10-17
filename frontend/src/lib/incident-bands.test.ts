import { describe, expect, it } from "vitest";

import type { ProviderIncident } from "@/lib/api";

import { clipBands } from "./incident-bands";

function incident(overrides: Partial<ProviderIncident>): ProviderIncident {
  return {
    id: "inc_1",
    provider: "anthropic",
    title: "Elevated errors on Claude Sonnet 4.5",
    impact: "major",
    status: "resolved",
    api_affected: true,
    components: ["API"],
    started_at: "2026-10-16T01:00:00Z",
    resolved_at: "2026-10-16T02:00:00Z",
    url: "https://status.anthropic.com/incidents/abc",
    latest_update: null,
    ...overrides,
  };
}

const window = {
  start: new Date("2026-10-16T00:00:00Z"),
  end: new Date("2026-10-16T12:00:00Z"),
};

describe("clipBands", () => {
  it("keeps an incident inside the window as it is", () => {
    expect(clipBands([incident({})], window)).toEqual([
      {
        start: new Date("2026-10-16T01:00:00Z"),
        end: new Date("2026-10-16T02:00:00Z"),
        title: "Elevated errors on Claude Sonnet 4.5",
        critical: false,
      },
    ]);
  });

  it("clips an incident on both sides of the window", () => {
    const [band] = clipBands(
      [incident({ started_at: "2026-10-15T20:00:00Z", resolved_at: "2026-10-16T18:00:00Z" })],
      window,
    );
    expect(band?.start).toEqual(window.start);
    expect(band?.end).toEqual(window.end);
  });

  it("ends an open incident at the window end", () => {
    const [band] = clipBands(
      [incident({ started_at: "2026-10-16T11:00:00Z", resolved_at: null })],
      window,
    );
    expect(band?.start).toEqual(new Date("2026-10-16T11:00:00Z"));
    expect(band?.end).toEqual(window.end);
  });

  it("drops incidents that do not overlap the window", () => {
    expect(
      clipBands(
        [
          incident({ started_at: "2026-10-15T01:00:00Z", resolved_at: "2026-10-15T23:00:00Z" }),
          incident({ started_at: "2026-10-16T13:00:00Z", resolved_at: null }),
          incident({ started_at: "2026-10-15T22:00:00Z", resolved_at: "2026-10-16T00:00:00Z" }),
        ],
        window,
      ),
    ).toEqual([]);
  });

  it("flags critical incidents", () => {
    const [band] = clipBands([incident({ impact: "critical" })], window);
    expect(band?.critical).toBe(true);
  });

  it("skips an incident whose times do not parse", () => {
    expect(clipBands([incident({ started_at: "not a date" })], window)).toEqual([]);
  });
});
