import { describe, expect, it } from "vitest";

import type { IncidentRef, ProviderHealth } from "@/lib/api";

import {
  formatRatio,
  formatRetryAfter,
  incidentBadge,
  kindRows,
  refusalShare,
  retrySummary,
  windowLength,
} from "./providers-format";

function provider(overrides: Partial<ProviderHealth>): ProviderHealth {
  return {
    provider: "anthropic",
    llm_calls: 8412,
    errors: 263,
    error_rate: "0.031265",
    by_class: [],
    rate_limit_kinds: [
      { kind: "requests", count: 31, with_retry_after: 31 },
      { kind: "tokens", count: 92, with_retry_after: 92 },
      { kind: "acceleration", count: 12, with_retry_after: 12 },
      { kind: "overloaded", count: 38, with_retry_after: 0 },
      { kind: "gateway", count: 6, with_retry_after: 6 },
      { kind: "unknown", count: 7, with_retry_after: 3 },
    ],
    retry_calls: 150,
    retry_cost_usd: "1.6370",
    retry_cost_unknown: false,
    incidents: [],
    ...overrides,
  };
}

function ref(overrides: Partial<IncidentRef>): IncidentRef {
  return {
    id: "inc_1",
    title: "Elevated errors",
    impact: "major",
    status: "resolved",
    started_at: "2026-10-16T01:12:00Z",
    resolved_at: "2026-10-16T02:05:00Z",
    url: "https://status.anthropic.com/incidents/1",
    ...overrides,
  };
}

describe("refusalShare", () => {
  it("counts provider 429s only, divided by failed calls", () => {
    const share = refusalShare(provider({}));
    expect(share.refusals).toBe(142);
    expect(share.share).toBeCloseTo(142 / 263);
  });

  it("is null without failed calls", () => {
    expect(refusalShare(provider({ errors: 0, rate_limit_kinds: [] })).share).toBeNull();
  });
});

describe("formatRatio", () => {
  it("formats with a no-break space before the percent sign", () => {
    expect(formatRatio(142 / 263, 1)).toBe("54.0 %");
    expect(formatRatio(0.031265, 2)).toBe("3.13 %");
    expect(formatRatio(null, 1)).toBeNull();
  });
});

describe("formatRetryAfter", () => {
  it("shows whole seconds plainly and fractions to one place", () => {
    expect(formatRetryAfter(12)).toBe("12 s");
    expect(formatRetryAfter(1.25)).toBe("1.3 s");
    expect(formatRetryAfter(null)).toBeNull();
  });
});

describe("incidentBadge", () => {
  it("says ongoing while any incident is open", () => {
    expect(incidentBadge([ref({}), ref({ id: "inc_2", resolved_at: null })])).toBe(
      "Incident ongoing",
    );
  });

  it("names the latest end time in UTC once every incident ended", () => {
    expect(
      incidentBadge([ref({}), ref({ id: "inc_2", resolved_at: "2026-10-16T00:30:00Z" })]),
    ).toBe("Incident ended 02:05\u00a0UTC");
  });

  it("is null without incidents", () => {
    expect(incidentBadge([])).toBeNull();
  });
});

describe("kindRows", () => {
  it("orders each provider's kinds by count with shares within that provider", () => {
    const rows = kindRows([provider({})]);
    expect(rows.map((row) => [row.kind, row.count])).toEqual([
      ["tokens", 92],
      ["overloaded", 38],
      ["requests", 31],
      ["acceleration", 12],
      ["unknown", 7],
      ["gateway", 6],
    ]);
    expect(rows[0]?.share).toBeCloseTo(92 / 186);
    expect(rows[4]?.retryShare).toBeCloseTo(3 / 7);
  });
});

describe("retrySummary", () => {
  it("adds every provider's retries", () => {
    const summary = retrySummary([
      provider({}),
      provider({ provider: "openai", retry_calls: 62, retry_cost_usd: "0.3042" }),
    ]);
    expect(summary).toMatchObject({ calls: 212, reason: null });
    expect(summary.cost).toBeCloseTo(1.9412);
    expect(summary.breakdown).toEqual([
      { label: "Anthropic", cost: 1.637 },
      { label: "OpenAI", cost: 0.3042 },
    ]);
  });

  it("is unknown when a retried call has no known cost", () => {
    const summary = retrySummary([
      provider({ retry_cost_usd: null, retry_cost_unknown: true }),
      provider({ provider: "openai", retry_calls: 62, retry_cost_usd: "0.3042" }),
    ]);
    expect(summary.cost).toBeNull();
    expect(summary.calls).toBe(212);
    expect(summary.reason).toBe("a retried call has no known cost");
    expect(summary.breakdown[0]).toEqual({ label: "Anthropic", cost: null });
  });

  it("is not computed when a trace predates retry tracking", () => {
    const summary = retrySummary([provider({ retry_calls: null, retry_cost_usd: null })]);
    expect(summary.calls).toBeNull();
    expect(summary.cost).toBeNull();
    expect(summary.reason).toBe("not computed for traces stored before 2026-10-15");
  });

  it("leaves providers without retries out of the breakdown", () => {
    const summary = retrySummary([provider({ retry_calls: 0, retry_cost_usd: "0" })]);
    expect(summary).toMatchObject({ calls: 0, cost: 0, breakdown: [] });
  });
});

describe("windowLength", () => {
  it("names whole days in days and anything else in hours", () => {
    expect(windowLength(7 * 86_400_000)).toBe("7 days");
    expect(windowLength(86_400_000)).toBe("1 day");
    expect(windowLength(36 * 3_600_000)).toBe("36 hours");
  });
});
