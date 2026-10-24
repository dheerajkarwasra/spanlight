import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { RunTotals, ScoreStat } from "@/lib/api";

import { RunScores } from "./run-scores";
import type { ScoreMeta } from "./score-meta";

function stat(overrides: Partial<ScoreStat>): ScoreStat {
  return {
    mean: null,
    count: 0,
    categories: null,
    passed: null,
    ci_low: null,
    ci_high: null,
    ...overrides,
  };
}

function totals(scores: Record<string, ScoreStat>, evaluatorErrors: number): RunTotals {
  return {
    items: 200,
    errors: 0,
    evaluator_errors: evaluatorErrors,
    cost_usd: "0.10",
    has_unpriced: false,
    latency_p50_ms: 100,
    latency_p95_ms: 200,
    scores,
  };
}

const RELEVANCE: ScoreMeta = {
  name: "relevance",
  kind: "numeric",
  evaluatorKind: "llm_judge",
  categories: [],
};
const FORMAT: ScoreMeta = {
  name: "format",
  kind: "boolean",
  evaluatorKind: "heuristic",
  categories: [],
};

function renderScores(run: RunTotals, scores: ScoreMeta[]) {
  render(
    <TooltipProvider>
      <RunScores totals={run} scores={scores} />
    </TooltipProvider>,
  );
}

describe("RunScores", () => {
  it("says how many items were scored and how many evaluator runs failed", () => {
    renderScores(totals({ relevance: stat({ mean: 0.82, count: 198 }) }, 2), [RELEVANCE]);
    expect(screen.getByText(/198 of 200 scored/)).toHaveTextContent("198 of 200 scored · 2 errors");
  });

  it("leaves the errors out of a score every item has", () => {
    renderScores(totals({ format: stat({ mean: 0.78, count: 200, passed: 156 }) }, 2), [FORMAT]);
    expect(screen.getByText(/156 yes/)).toHaveTextContent(/^156 yes · 44 no · 200 of 200 scored$/);
  });

  it("says the denominator without errors when none failed", () => {
    renderScores(totals({ relevance: stat({ mean: 0.82, count: 150 }) }, 0), [RELEVANCE]);
    expect(screen.getByText(/150 of 200 scored/)).toHaveTextContent(/^150 of 200 scored$/);
  });
});
