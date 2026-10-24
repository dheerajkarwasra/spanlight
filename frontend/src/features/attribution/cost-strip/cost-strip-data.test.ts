import { describe, expect, it } from "vitest";

import type { ReconciliationDay, ReconciliationReport, TokenMix } from "@/lib/api";
import { resolveRange } from "@/lib/time-range";

import { response } from "../attribution-test-data";
import {
  aggregateSources,
  costStripCards,
  mixCaption,
  reportWindow,
  tokenShares,
  type Billing,
  type SourceAggregate,
} from "./cost-strip-data";

const NOW = new Date("2026-10-21T12:00:00Z");
const TODAY = "2026-10-21";

function day(date: string, overrides: Partial<ReconciliationDay> = {}): ReconciliationDay {
  return {
    day: date,
    provisional: false,
    fetched_at: `${date}T06:00:00Z`,
    incomplete: false,
    note: null,
    provider_usd: "10.00",
    spanlight_usd: "9.00",
    explained_usd: "0.60",
    residual_usd: "0.40",
    variance_ratio: "0.0400",
    credits_usd: null,
    other_usd: null,
    unattributed_calls: 0,
    groups: [],
    credits: [],
    other: [],
    ...overrides,
  };
}

function report(days: ReconciliationDay[], projects = 1): ReconciliationReport {
  return {
    source: { id: "s1", provider: "openai", kind: "openai_costs_api" },
    window: { start: "2026-10-14T00:00:00Z", end: "2026-10-21T00:00:00Z" },
    projects: Array.from({ length: projects }, (_, index) => ({
      id: `p${String(index)}`,
      name: `Project ${String(index)}`,
    })),
    days,
    summary: {
      days: days.length,
      settled_days: 0,
      days_within_2pct: 0,
      max_abs_variance_ratio: null,
    },
  };
}

describe("reportWindow", () => {
  it("snaps a 7-day range to whole UTC days ending today", () => {
    const range = resolveRange({ range: "7d" }, NOW);
    expect(reportWindow(range, TODAY)).toEqual({
      first: "2026-10-14",
      last: "2026-10-21",
      capped: false,
    });
  });

  it("caps a 90-day range at the last 31 days", () => {
    const range = resolveRange(
      { range: "custom", from: "2026-07-23T12:00:00Z", to: "2026-10-21T12:00:00Z" },
      NOW,
    );
    expect(reportWindow(range, TODAY)).toEqual({
      first: "2026-09-21",
      last: "2026-10-21",
      capped: true,
    });
  });

  it("ends a range that runs past today at today", () => {
    const range = {
      value: "custom" as const,
      from: "2026-10-19T08:00:00.000Z",
      to: "2026-10-23T00:00:00.000Z",
      durationMs: 0,
    };
    expect(reportWindow(range, TODAY)).toEqual({
      first: "2026-10-19",
      last: "2026-10-21",
      capped: false,
    });
  });

  it("returns null for a range that starts after today", () => {
    const range = {
      value: "custom" as const,
      from: "2026-10-22T00:00:00.000Z",
      to: "2026-10-23T00:00:00.000Z",
      durationMs: 0,
    };
    expect(reportWindow(range, TODAY)).toBeNull();
  });
});

describe("aggregateSources", () => {
  it("sums one source over the days with provider data only", () => {
    const result = aggregateSources([
      {
        name: "OpenAI prod",
        report: report([
          day("2026-10-18"),
          day("2026-10-19"),
          day("2026-10-20", { provider_usd: null, spanlight_usd: "5.00", residual_usd: null }),
        ]),
      },
    ]);
    expect(result).toMatchObject({
      providerUsd: "20",
      spanlightUsd: "18",
      explainedUsd: "1.2",
      residualUsd: "0.8",
      lastSettledDay: "2026-10-19",
      provisionalDays: 0,
      projectCount: 1,
      names: ["OpenAI prod"],
    });
    expect(result.variance).toBeCloseTo(0.04);
  });

  it("sums two sources and labels the widest project count", () => {
    const result = aggregateSources([
      { name: "OpenAI prod", report: report([day("2026-10-19")], 2) },
      {
        name: "Anthropic",
        report: report([day("2026-10-19", { provider_usd: "30.00", spanlight_usd: "31.00" })], 3),
      },
    ]);
    expect(result.providerUsd).toBe("40");
    expect(result.spanlightUsd).toBe("40");
    expect(result.variance).toBeCloseTo(0.02);
    expect(result.projectCount).toBe(3);
    expect(result.names).toEqual(["OpenAI prod", "Anthropic"]);
  });

  it("counts provisional days and keeps the last settled one", () => {
    const result = aggregateSources([
      {
        name: "OpenAI prod",
        report: report([day("2026-10-19"), day("2026-10-20", { provisional: true })]),
      },
    ]);
    expect(result.provisionalDays).toBe(1);
    expect(result.lastSettledDay).toBe("2026-10-19");
  });

  it("is unknown, not zero, when no day has provider data", () => {
    const result = aggregateSources([
      { name: "OpenAI prod", report: report([day("2026-10-20", { provider_usd: null })]) },
    ]);
    expect(result.providerUsd).toBeNull();
    expect(result.variance).toBeNull();
    expect(result.lastSettledDay).toBeNull();
  });
});

const MIX: TokenMix = {
  first_day: "2026-10-14",
  last_day: "2026-10-20",
  uncached_input_tokens: 6100,
  output_tokens: 2200,
  cache_read_tokens: 1500,
  cache_write_tokens: 200,
  fee_usd: "1.50",
  cost_usd: "375.05",
};

describe("tokenShares", () => {
  it("gives four shares that add up to 100", () => {
    const shares = tokenShares({
      ...MIX,
      uncached_input_tokens: 1,
      output_tokens: 1,
      cache_read_tokens: 1,
    });
    expect(shares?.map((share) => share.key)).toEqual([
      "input",
      "output",
      "cache_read",
      "cache_write",
    ]);
    expect(shares?.reduce((sum, share) => sum + share.pct, 0)).toBe(100);
    expect(tokenShares(MIX)?.map((share) => share.pct)).toEqual([61, 22, 15, 2]);
  });

  it("is null when no token was counted or a class is unknown", () => {
    const none = {
      uncached_input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
    };
    expect(tokenShares({ ...MIX, ...none })).toBeNull();
    expect(tokenShares({ ...MIX, output_tokens: null })).toBeNull();
  });

  it("captions the days it covers", () => {
    expect(mixCaption(MIX)).toBe("All environments · Oct 14–20 UTC");
    expect(mixCaption({ ...MIX, first_day: "2026-09-28" })).toBe(
      "All environments · Sep 28 – Oct 20 UTC",
    );
  });
});

const AGGREGATE: SourceAggregate = {
  providerUsd: "368.20",
  spanlightUsd: "375.05",
  explainedUsd: "4.25",
  residualUsd: "2.75",
  variance: -0.0186,
  lastSettledDay: "2026-10-19",
  provisionalDays: 1,
  projectCount: 2,
  names: ["OpenAI prod", "Anthropic"],
};

function cards(billing: Billing, unpriced = 0) {
  const attribution = response({
    totals: { ...response().totals, cost_usd: "375.05", unpriced_calls: unpriced },
  });
  return costStripCards({ attribution, attributionFailed: false, billing });
}

describe("costStripCards", () => {
  it("reads the billing source figures", () => {
    const [, billed, variance] = cards({ state: "ready", aggregate: AGGREGATE, capped: false });
    expect(billed).toMatchObject({
      value: "$368.20",
      lines: ["OpenAI prod, Anthropic · through Oct 19", "1 day provisional", "Across 2 projects"],
      href: "reconciliation",
    });
    expect(variance).toMatchObject({
      value: "−1.9 %",
      tone: "within2",
      lines: ["Within 2 %", "Explained $4.25 · unexplained $2.75"],
    });
  });

  it("says when the window was capped", () => {
    const [, billed] = cards({ state: "ready", aggregate: AGGREGATE, capped: true });
    expect(billed?.lines).toContain("Last 31 days");
  });

  it("offers to connect a source when none maps the project", () => {
    const [, billed, variance] = cards({ state: "not_connected" });
    expect(billed).toMatchObject({
      value: "Not connected",
      lines: ["Connect a billing source"],
      href: "billing-sources",
    });
    expect(variance).toMatchObject({ value: null, href: "billing-sources" });
  });

  it("has no variance when the provider billed $0, and says so", () => {
    const aggregate = aggregateSources([
      {
        name: "OpenAI prod",
        report: report([day("2026-10-19", { provider_usd: "0", residual_usd: "-9.00" })]),
      },
    ]);
    expect(aggregate.providerUsd).toBe("0");
    expect(aggregate.variance).toBeNull();
    const [, billed, variance] = cards({ state: "ready", aggregate, capped: false });
    expect(billed?.value).toBe("$0.00");
    expect(variance).toMatchObject({
      value: null,
      unknownReason: "The provider has billed nothing for these days yet.",
    });
  });

  it("says Spanlight's side is still being built when billed days have no residual", () => {
    const pending = { spanlight_usd: null, explained_usd: null, residual_usd: null };
    const aggregate = aggregateSources([
      {
        name: "OpenAI prod",
        report: report([
          day("2026-10-19", { ...pending, variance_ratio: null, note: "rollup pending" }),
        ]),
      },
    ]);
    expect(aggregate.providerUsd).toBe("10");
    const [, , variance] = cards({ state: "ready", aggregate, capped: false });
    expect(variance).toMatchObject({
      value: null,
      unknownReason: "Spanlight's figures for these days are still being built.",
    });
  });

  it("explains a hidden organization instead of a figure", () => {
    const [, billed, variance] = cards({ state: "forbidden" });
    const reason = "You can't see this organization's billing sources.";
    expect(billed).toMatchObject({ value: null, unknownReason: reason, retry: false });
    expect(variance).toMatchObject({ value: null, unknownReason: reason, retry: false });
  });

  it("offers a retry when the billing data failed", () => {
    const [, billed] = cards({ state: "failed" });
    expect(billed).toMatchObject({
      value: null,
      unknownReason: "Couldn't load billing data",
      retry: true,
    });
  });

  it("marks spend as a lower bound only while calls are unpriced", () => {
    const [spend, , , unpriced] = cards({ state: "not_connected" }, 118);
    expect(spend).toMatchObject({ value: "$375.05", lowerBound: true });
    expect(unpriced).toMatchObject({ value: "118", lines: ["Spend above is a lower bound"] });

    const [priced, , , none] = cards({ state: "not_connected" }, 0);
    expect(priced?.lowerBound).toBe(false);
    expect(none).toMatchObject({ value: "0", lines: ["Every call has a price"] });
  });
});
