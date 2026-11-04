import { expect, it } from "vitest";
import { toChartPoints, summarizeCalls } from "./chart-data";
import { toStackedPoints } from "../providers/error-series";

it("keeps missing historical chart counts and their sums unknown", () => {
  const points = toChartPoints(
    [
      {
        bucket_start: "2026-10-01T00:00:00Z",
        llm_calls: null,
        errors: null,
        tokens: null,
        cost_usd: null,
        p95_ms: null,
        approximate: true,
      },
    ],
    { from: "2026-10-01T00:00:00Z", to: "2026-10-02T00:00:00Z", durationMs: 86400000 },
  );
  expect(points[0]?.succeeded).toBeNull();
  expect(summarizeCalls(points)).toEqual({ calls: null, errors: null });
});
it("keeps unavailable error-class buckets unknown beside known classes", () => {
  const points = toStackedPoints([
    { bucket_start: "a", by_class: { timeout: 2 }, approximate: true },
    { bucket_start: "b", by_class: null, approximate: true },
  ]);
  expect(points[1]?.counts.timeout).toBeNull();
  expect(points[1]?.total).toBeNull();
});

it.each([0, 1, 13, 1000000])(
  "preserves known and missing call totals under bucket order for %i calls",
  (calls) => {
    const known = {
      bucket_start: "2026-10-01T00:00:00Z",
      llm_calls: calls,
      errors: 0,
      tokens: calls,
      cost_usd: "0",
      p95_ms: null,
      approximate: true,
    };
    const unknown = { ...known, llm_calls: null, errors: null, tokens: null, cost_usd: null };
    const range = {
      from: "2026-10-01T00:00:00Z",
      to: "2026-10-02T00:00:00Z",
      durationMs: 86400000,
    };
    expect(summarizeCalls(toChartPoints([known, unknown], range))).toEqual({
      calls: null,
      errors: null,
    });
    expect(summarizeCalls(toChartPoints([unknown, known], range))).toEqual({
      calls: null,
      errors: null,
    });
    expect(summarizeCalls(toChartPoints([known, known], range))).toEqual({
      calls: calls * 2,
      errors: 0,
    });
  },
);

it("does not classify missing daily counts as a window without calls", async () => {
  const { hasDailyCalls, toDailyPoints } = await import("../users/user-format");
  expect(
    hasDailyCalls(
      toDailyPoints([
        {
          day: "2026-10-01",
          traces: null,
          llm_calls: null,
          errors: null,
          unpriced_calls: null,
          tokens: null,
          cost_usd: null,
        },
      ]),
    ),
  ).toBe(true);
});
