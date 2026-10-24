import { describe, expect, it } from "vitest";

import type { ModelMetrics } from "@/lib/api";

import { spendByModel } from "./models";

function model(name: string, cost: string | null, calls = 1): ModelMetrics {
  return { model: name, provider: "openai", cost_usd: cost, calls } as ModelMetrics;
}

describe("spendByModel", () => {
  it("ranks priced models by cost with their share of priced spend, unpriced after with no share", () => {
    expect(
      spendByModel([model("cheap", "1.00"), model("unpriced", null, 40), model("dear", "3.00")]),
    ).toEqual([
      { model: "dear", provider: "openai", costUsd: 3, share: 0.75 },
      { model: "cheap", provider: "openai", costUsd: 1, share: 0.25 },
      { model: "unpriced", provider: "openai", costUsd: null, share: null },
    ]);
  });

  it("keeps the top five", () => {
    const models = Array.from({ length: 7 }, (_, index) => model(`m${index}`, String(index + 1)));
    const rows = spendByModel(models);
    expect(rows).toHaveLength(5);
    expect(rows[0]?.model).toBe("m6");
  });
});
