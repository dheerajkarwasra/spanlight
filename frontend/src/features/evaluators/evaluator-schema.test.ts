import { describe, expect, it } from "vitest";

import {
  BUDGET_REQUIRED_COPY,
  EMPTY_EVALUATOR_VALUES,
  budgetProblem,
  evaluatorFormSchema,
  splitNames,
  type EvaluatorFormValues,
} from "./evaluator-schema";
import {
  evaluatorFieldOf,
  percentOf,
  toEvaluatorCreate,
  toEvaluatorUpdate,
  toSamplingRate,
} from "./evaluator-values";

// Bounds from `app/evals/config.py`: names match the score-name pattern, a judge's max_tokens is
// 1..4096, temperature 0..2 with 2 decimals, the budget is numeric(12,4) above 0, sampling is a
// rate with 4 decimals.

function form(overrides: Partial<EvaluatorFormValues>): EvaluatorFormValues {
  return { ...EMPTY_EVALUATOR_VALUES, name: "has-citation", pattern: "\\[\\d+\\]", ...overrides };
}

const JUDGE: Partial<EvaluatorFormValues> = {
  kind: "llm_judge",
  credentialId: "cred_1",
  model: "claude-haiku-4-5",
  budget: "5",
};

function issuePaths(values: EvaluatorFormValues): string[] {
  const result = evaluatorFormSchema.safeParse(values);
  return result.success ? [] : result.error.issues.map((issue) => issue.path.join("."));
}

describe("evaluator names", () => {
  it("follow the score-name rule", () => {
    expect(issuePaths(form({}))).toEqual([]);
    expect(issuePaths(form({ name: "Has Citation" }))).toEqual(["name"]);
    expect(issuePaths(form({ name: "_private" }))).toEqual(["name"]);
    expect(issuePaths(form({ name: "a".repeat(65) }))).toEqual(["name"]);
    expect(issuePaths(form({ name: "v1.2_ok-name" }))).toEqual([]);
  });
});

describe("heuristic evaluators", () => {
  it("need a pattern for regex and a needle for contains", () => {
    expect(issuePaths(form({ pattern: "  " }))).toEqual(["pattern"]);
    expect(issuePaths(form({ rule: "contains", needle: "" }))).toEqual(["needle"]);
  });

  it("refuse to sample live traces with a rule that needs an expected output", () => {
    expect(issuePaths(form({ rule: "exact_match", samplingPercent: 10 }))).toEqual([
      "samplingPercent",
    ]);
    expect(issuePaths(form({ rule: "levenshtein", samplingPercent: 0 }))).toEqual([]);
  });

  it("never sends a sampling rate above 0 for such a rule", () => {
    const body = toEvaluatorCreate(form({ rule: "exact_match", samplingPercent: 50 }));
    expect(body.sampling_rate).toBe("0");
    expect(body.monthly_budget_usd).toBeNull();
  });
});

describe("judge evaluators", () => {
  it("need a credential, a model and a budget", () => {
    expect(issuePaths(form({ kind: "llm_judge" })).sort()).toEqual(
      ["budget", "credentialId", "model"].sort(),
    );
    expect(issuePaths(form(JUDGE))).toEqual([]);
  });

  it("explain why a budget is required", () => {
    expect(budgetProblem("  ")).toBe(BUDGET_REQUIRED_COPY);
  });

  it("bound max_tokens to whole numbers 1..4096", () => {
    for (const maxTokens of ["0", "4097", "1.5", "", "-1"]) {
      expect(issuePaths(form({ ...JUDGE, maxTokens }))).toEqual(["maxTokens"]);
    }
    expect(issuePaths(form({ ...JUDGE, maxTokens: "4096" }))).toEqual([]);
  });

  it("bound temperature to 0..2 with at most 2 decimals, blank meaning the provider default", () => {
    for (const temperature of ["", "0", "0.7", "2", "2.00", ".5", "1.99"]) {
      expect(issuePaths(form({ ...JUDGE, temperature }))).toEqual([]);
    }
    for (const temperature of ["2.01", "3", "0.123", "-0.1", "abc"]) {
      expect(issuePaths(form({ ...JUDGE, temperature }))).toEqual(["temperature"]);
    }
  });

  it("bound the budget to numeric(12,4) above 0", () => {
    expect(budgetProblem("0")).toBe("Enter a budget greater than 0.");
    expect(budgetProblem("0.00001")).toBe("Use at most 4 decimal places.");
    expect(budgetProblem("99999999.9999")).toBeNull();
    expect(budgetProblem("100000000")).toBe("Use a budget below $100,000,000.");
  });

  it("send a normalised budget and an unset temperature as null", () => {
    const body = toEvaluatorCreate(form({ ...JUDGE, budget: " .5 " }));
    expect(body.monthly_budget_usd).toBe("0.5");
    expect(body.config).toMatchObject({ temperature: null, max_tokens: 1024 });
  });
});

describe("filters", () => {
  it("split trace names on commas, trimmed and without repeats", () => {
    expect(splitNames(" chat, ,search,chat ")).toEqual(["chat", "search"]);
  });

  it("refuse more than 50 names and a non-integer minimum duration", () => {
    const many = Array.from({ length: 51 }, (_, index) => `n${String(index)}`).join(",");
    expect(issuePaths(form({ traceNames: many }))).toEqual(["traceNames"]);
    expect(issuePaths(form({ minDurationMs: "1.5" }))).toEqual(["minDurationMs"]);
  });

  it("send an empty minimum duration as null, not 0", () => {
    expect(toEvaluatorCreate(form({ minDurationMs: " " })).filters?.min_duration_ms).toBeNull();
    expect(toEvaluatorCreate(form({ minDurationMs: "0" })).filters?.min_duration_ms).toBe(0);
  });
});

describe("sampling rate", () => {
  it("converts percent to the stored 4-decimal rate and back without float noise", () => {
    expect(toSamplingRate(5)).toBe("0.05");
    expect(toSamplingRate(0.5)).toBe("0.005");
    expect(toSamplingRate(33.33)).toBe("0.3333");
    expect(percentOf("0.0050")).toBe(0.5);
    expect(percentOf("0.3333")).toBe(33.33);
    expect(percentOf("1.0000")).toBe(100);
  });
});

describe("toEvaluatorUpdate", () => {
  it("sends nothing for an untouched edit", () => {
    const initial = form({ ...JUDGE, budget: "5.0000", temperature: "0.7" });
    expect(toEvaluatorUpdate({ ...initial }, initial)).toEqual({});
  });

  it("sends a changed config whole, without its kind", () => {
    const initial = form({});
    const update = toEvaluatorUpdate(form({ pattern: "cite" }), initial);
    expect(Object.keys(update)).toEqual(["config"]);
    expect(update.config).toMatchObject({ check: { rule: "regex", pattern: "cite" } });
    expect(update.config).not.toHaveProperty("kind");
  });
});

describe("evaluatorFieldOf", () => {
  it("strips pydantic's union tags from server error paths", () => {
    expect(evaluatorFieldOf("config.llm_judge.temperature")).toBe("temperature");
    expect(evaluatorFieldOf("config.heuristic.check.regex.pattern")).toBe("pattern");
    expect(evaluatorFieldOf("monthly_budget_usd")).toBe("budget");
    expect(evaluatorFieldOf("filters.tags.0")).toBeNull();
  });
});
