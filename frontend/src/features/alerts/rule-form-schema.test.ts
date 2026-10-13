import { describe, expect, it } from "vitest";

import type { AlertRule } from "@/lib/api";

import {
  EMPTY_RULE_VALUES,
  definitionIssues,
  ruleFieldOf,
  ruleFormIssues,
  toPreviewInput,
  toRuleInput,
  valuesFromRule,
  type RuleFormValues,
} from "./rule-form-schema";

// Mirrors the server's `RuleSpec` (`app/alerts/rule_spec.py`): a form that passes here must not
// be refused by the API, and half-typed numbers must never be sent as 0.

function rule(overrides: Partial<RuleFormValues>): RuleFormValues {
  return { ...EMPTY_RULE_VALUES, name: "Errors", threshold: "5", ...overrides };
}

function fields(values: RuleFormValues): string[] {
  return ruleFormIssues(values).map(([field]) => field);
}

describe("ruleFormIssues", () => {
  it("accepts a complete threshold rule", () => {
    expect(ruleFormIssues(rule({}))).toEqual([]);
  });

  it("refuses a blank threshold rather than treating it as 0", () => {
    expect(fields(rule({ threshold: "" }))).toEqual(["threshold"]);
  });

  it("does not check the threshold of an anomaly rule, but checks its baseline and sensitivity", () => {
    const anomaly = rule({
      kind: "anomaly",
      threshold: "",
      baselineWindows: "2",
      sensitivity: "5.01",
    });
    expect(fields(anomaly)).toEqual(["baselineWindows", "sensitivity"]);
    expect(
      fields(rule({ kind: "anomaly", threshold: "", baselineWindows: "30", sensitivity: "0.5" })),
    ).toEqual([]);
  });

  it("refuses a sensitivity with more than 2 decimals, as the server does", () => {
    expect(fields(rule({ kind: "anomaly", sensitivity: "1.234" }))).toEqual(["sensitivity"]);
  });

  it("bounds the window to 5..1440 minutes and the cooldown to 0..1440", () => {
    expect(fields(rule({ windowMinutes: 4 }))).toEqual(["windowMinutes"]);
    expect(fields(rule({ windowMinutes: 1441 }))).toEqual(["windowMinutes"]);
    expect(fields(rule({ cooldownMinutes: "0" }))).toEqual([]);
    expect(fields(rule({ cooldownMinutes: "" }))).toEqual(["cooldownMinutes"]);
    expect(fields(rule({ cooldownMinutes: "1.5" }))).toEqual(["cooldownMinutes"]);
    expect(fields(rule({ cooldownMinutes: "1441" }))).toEqual(["cooldownMinutes"]);
  });

  it("checks the name after trimming, up to 80 characters", () => {
    expect(fields(rule({ name: "   " }))).toEqual(["name"]);
    expect(fields(rule({ name: ` ${"n".repeat(80)} ` }))).toEqual([]);
    expect(fields(rule({ name: "n".repeat(81) }))).toEqual(["name"]);
  });

  it("limits filters to 200 characters and channels to 10", () => {
    expect(fields(rule({ model: "m".repeat(201) }))).toEqual(["model"]);
    const eleven = Array.from({ length: 11 }, (_, index) => `ch_${String(index)}`);
    expect(fields(rule({ channelIds: eleven }))).toEqual(["channelIds"]);
  });

  it("lets the name and channels be unfinished without blocking the preview", () => {
    expect(definitionIssues(rule({ name: "", channelIds: [] }))).toEqual([]);
  });
});

describe("request bodies", () => {
  it("send a threshold rule with no anomaly fields, in API units", () => {
    expect(toPreviewInput(rule({ threshold: "5", environment: " prod ", provider: "  " }))).toEqual(
      {
        kind: "threshold",
        metric: "error_rate",
        comparator: "gt",
        threshold: "0.05",
        window_minutes: 15,
        baseline_windows: null,
        sensitivity: null,
        filters: { environment: "prod" },
      },
    );
  });

  it("send an anomaly rule with no threshold", () => {
    const body = toPreviewInput(
      rule({ kind: "anomaly", baselineWindows: " 12 ", sensitivity: "2.5" }),
    );
    expect(body).toMatchObject({ threshold: null, baseline_windows: 12, sensitivity: "2.5" });
  });

  it("trim the name and drop duplicate channels on save", () => {
    const body = toRuleInput(
      rule({ name: "  Errors  ", channelIds: ["a", "b", "a"], cooldownMinutes: "0" }),
    );
    expect(body).toMatchObject({ name: "Errors", channel_ids: ["a", "b"], cooldown_minutes: 0 });
  });
});

describe("valuesFromRule", () => {
  it("loads a stored rule back into the same body it was saved from", () => {
    const stored: AlertRule = {
      id: "r1",
      project_id: "p1",
      name: "Slow",
      kind: "threshold",
      metric: "p95_ms",
      comparator: "gte",
      threshold: "1500.000000",
      window_minutes: 30,
      baseline_windows: null,
      sensitivity: null,
      filters: { model: "gpt-4.1" },
      cooldown_minutes: 0,
      enabled: false,
      channel_ids: ["c1"],
      muted_until: null,
      created_by: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
      state: null,
    };
    const form = valuesFromRule(stored);
    expect(form.threshold).toBe("1.5");
    expect(ruleFormIssues(form)).toEqual([]);
    expect(toRuleInput(form)).toMatchObject({
      threshold: "1500",
      cooldown_minutes: 0,
      enabled: false,
      filters: { model: "gpt-4.1" },
    });
  });
});

describe("ruleFieldOf", () => {
  it("maps server error paths onto form fields", () => {
    expect(ruleFieldOf("window_minutes")).toBe("windowMinutes");
    expect(ruleFieldOf("filters.provider")).toBe("provider");
    expect(ruleFieldOf("channel_ids.2")).toBe("channelIds");
    expect(ruleFieldOf("unknown")).toBeNull();
  });
});
