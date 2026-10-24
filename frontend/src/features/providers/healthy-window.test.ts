import { describe, expect, it } from "vitest";

import type { HealthReport, ProviderHealth } from "@/lib/api";

import { isHealthyWindow } from "./healthy-window";

const PROVIDER: ProviderHealth = {
  provider: "openai",
  llm_calls: 10,
  errors: 0,
  error_rate: "0",
  by_class: [],
  rate_limit_kinds: [],
  retry_calls: 0,
  retry_cost_usd: "0",
  retry_cost_unknown: false,
  incidents: [],
};

function report(providers: ProviderHealth[]): HealthReport {
  return {
    window: { start: "2026-10-15T00:00:00Z", end: "2026-10-16T00:00:00Z" },
    approximate: false,
    providers,
    retry_cost_note: "",
  };
}

describe("isHealthyWindow", () => {
  it.each([
    ["no providers", [], false],
    ["every provider without errors", [PROVIDER, { ...PROVIDER, provider: null }], true],
    [
      "one provider with an error",
      [PROVIDER, { ...PROVIDER, provider: "anthropic", errors: 1 }],
      false,
    ],
  ])("%s → %s", (_, providers, expected) => {
    expect(isHealthyWindow(report(providers))).toBe(expected);
  });
});
