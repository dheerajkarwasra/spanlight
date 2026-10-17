import { describe, expect, it } from "vitest";

import type { TraceSummary } from "@/lib/api";

import {
  averageCostPerTurn,
  consistentUserId,
  modelUsage,
  sortChronologically,
  spanBetween,
  summariseSession,
  sumTokens,
  timelinePoints,
  turnsOutsideEnvironment,
} from "./session-summary";

function makeTrace(
  id: string,
  startedAt: string,
  endedAt: string,
  overrides: Partial<TraceSummary> = {},
): TraceSummary {
  return {
    trace_id: id,
    name: id,
    environment: null,
    release: null,
    external_user_id: null,
    session_id: "s1",
    tags: [],
    started_at: startedAt,
    ended_at: endedAt,
    duration_ms: 10,
    span_count: 1,
    error_count: 0,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: null,
    has_unpriced: false,
    models: [],
    error_message: null,
    has_scores: false,
    scores: [],
    error_class: null,
    retry_calls: 0,
    metadata: {},
    retry_cost_usd: "0",
    ...overrides,
  };
}

describe("summariseSession", () => {
  it("sums known costs and errors and measures the time span", () => {
    const totals = summariseSession([
      makeTrace("b", "2026-10-07T12:05:00Z", "2026-10-07T12:05:02Z", {
        cost_usd: "0.0020",
        error_count: 1,
      }),
      makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:03Z", { cost_usd: "0.0010" }),
    ]);

    expect(totals.turns).toBe(2);
    expect(totals.costUsd).toBeCloseTo(0.003);
    expect(totals.costIsLowerBound).toBe(false);
    expect(totals.errors).toBe(1);
    expect(totals.firstAt).toBe("2026-10-07T12:00:00Z");
    expect(totals.lastAt).toBe("2026-10-07T12:05:02Z");
    expect(totals.durationMs).toBe(302_000);
  });

  it("marks the total as a lower bound when some costs are unknown or partial", () => {
    const withUnknown = summariseSession([
      makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z", { cost_usd: "0.5" }),
      makeTrace("b", "2026-10-07T12:01:00Z", "2026-10-07T12:01:01Z", { cost_usd: null }),
    ]);
    const withPartial = summariseSession([
      makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z", {
        cost_usd: "0.5",
        has_unpriced: true,
      }),
    ]);

    expect(withUnknown).toMatchObject({ costUsd: 0.5, costIsLowerBound: true });
    expect(withPartial).toMatchObject({ costUsd: 0.5, costIsLowerBound: true });
  });

  it("reports an unknown cost, not zero, when no trace is priced", () => {
    const totals = summariseSession([
      makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z"),
    ]);

    expect(totals.costUsd).toBeNull();
    expect(totals.costIsLowerBound).toBe(false);
  });

  it("handles an empty session", () => {
    expect(summariseSession([])).toEqual({
      turns: 0,
      costUsd: null,
      costIsLowerBound: false,
      errors: 0,
      firstAt: null,
      lastAt: null,
      durationMs: null,
    });
  });
});

describe("sortChronologically", () => {
  it("orders oldest first without mutating the input", () => {
    const input = [
      makeTrace("new", "2026-10-07T12:05:00Z", "2026-10-07T12:05:01Z"),
      makeTrace("old", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z"),
    ];

    expect(sortChronologically(input).map((trace) => trace.trace_id)).toEqual(["old", "new"]);
    expect(input[0]?.trace_id).toBe("new");
  });
});

describe("spanBetween", () => {
  it("measures elapsed time and rejects invalid dates", () => {
    expect(spanBetween("2026-10-07T12:00:00Z", "2026-10-07T12:01:00Z")).toBe(60_000);
    expect(spanBetween("nope", "2026-10-07T12:01:00Z")).toBeNull();
  });
});

describe("sumTokens", () => {
  it("adds input and output tokens across turns", () => {
    const tokens = sumTokens([
      makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z", {
        input_tokens: 100,
        output_tokens: 20,
      }),
      makeTrace("b", "2026-10-07T12:01:00Z", "2026-10-07T12:01:01Z", {
        input_tokens: 50,
        output_tokens: 5,
      }),
    ]);

    expect(tokens).toEqual({ input: 150, output: 25, total: 175 });
  });
});

describe("modelUsage", () => {
  it("counts each model once per turn, most used first", () => {
    const usage = modelUsage([
      makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z", {
        models: ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-sonnet-4-5"],
      }),
      makeTrace("b", "2026-10-07T12:01:00Z", "2026-10-07T12:01:01Z", {
        models: ["claude-sonnet-4-5"],
      }),
    ]);

    expect(usage).toEqual([
      { model: "claude-sonnet-4-5", turns: 2 },
      { model: "claude-haiku-4-5", turns: 1 },
    ]);
  });
});

describe("consistentUserId", () => {
  it("returns the user when every trace that names one agrees", () => {
    expect(
      consistentUserId([
        makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z", {
          external_user_id: "user_1",
        }),
        makeTrace("b", "2026-10-07T12:01:00Z", "2026-10-07T12:01:01Z"),
      ]),
    ).toBe("user_1");
  });

  it("returns null when the traces disagree or name nobody", () => {
    expect(
      consistentUserId([
        makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z", {
          external_user_id: "user_1",
        }),
        makeTrace("b", "2026-10-07T12:01:00Z", "2026-10-07T12:01:01Z", {
          external_user_id: "user_2",
        }),
      ]),
    ).toBeNull();
    expect(
      consistentUserId([makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z")]),
    ).toBeNull();
  });
});

describe("averageCostPerTurn", () => {
  const base = summariseSession([]);

  it("divides a complete total by the number of turns", () => {
    expect(averageCostPerTurn({ ...base, turns: 4, costUsd: 0.2 })).toBeCloseTo(0.05);
  });

  it("is unknown for partial or missing totals", () => {
    expect(
      averageCostPerTurn({ ...base, turns: 4, costUsd: 0.2, costIsLowerBound: true }),
    ).toBeNull();
    expect(averageCostPerTurn({ ...base, turns: 4, costUsd: null })).toBeNull();
  });
});

describe("timelinePoints", () => {
  it("places each turn between the first and last start and flags failures", () => {
    const points = timelinePoints([
      makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z"),
      makeTrace("b", "2026-10-07T12:01:00Z", "2026-10-07T12:01:01Z", { error_count: 1 }),
      makeTrace("c", "2026-10-07T12:04:00Z", "2026-10-07T12:04:01Z"),
    ]);

    expect(points).toEqual([
      { traceId: "a", offset: 0, failed: false },
      { traceId: "b", offset: 0.25, failed: true },
      { traceId: "c", offset: 1, failed: false },
    ]);
  });

  it("centres a single turn", () => {
    expect(
      timelinePoints([makeTrace("a", "2026-10-07T12:00:00Z", "2026-10-07T12:00:01Z")]),
    ).toEqual([{ traceId: "a", offset: 0.5, failed: false }]);
  });
});

describe("turnsOutsideEnvironment", () => {
  const traces = [
    makeTrace("a", "2026-10-14T09:00:00Z", "2026-10-14T09:00:01Z", { environment: "production" }),
    makeTrace("b", "2026-10-14T09:01:00Z", "2026-10-14T09:01:01Z", { environment: "staging" }),
    makeTrace("c", "2026-10-14T09:02:00Z", "2026-10-14T09:02:01Z"),
  ];

  it("is 0 when no environment is selected", () => {
    expect(turnsOutsideEnvironment(traces, undefined)).toBe(0);
  });

  it("counts other environments and traces without one", () => {
    expect(turnsOutsideEnvironment(traces, "production")).toBe(2);
    expect(turnsOutsideEnvironment(traces, "staging")).toBe(2);
  });
});
