import { describe, expect, it } from "vitest";

import { dataFiltersFor, hasEnabledFilter } from "./use-data-filters";

const P = "/o/p";

describe("dataFiltersFor", () => {
  it.each([
    "overview",
    "traces",
    "sessions",
    "users",
    "users/u1",
    "providers",
    "releases",
    "attribution",
    "reconciliation",
    "pricing",
  ])("turns both controls on for /%s", (page) => {
    const options = dataFiltersFor(`${P}/${page}`);
    expect(options.rangeDisabled).toBeUndefined();
    expect(options.environmentDisabled).toBeUndefined();
    expect(options.presetNote).toBeUndefined();
    expect(options.presets).toEqual(["1h", "24h", "7d", "30d"]);
    expect(options.allowCustom).toBe(true);
  });

  it.each([
    // [path, range reason, environment reason]
    [
      `${P}/gateway/keys`,
      "Gateway settings aren't filtered by time.",
      "Gateway settings aren't filtered by environment.",
    ],
    [
      `${P}/gateway/routes/r`,
      "Gateway settings aren't filtered by time.",
      "Gateway settings aren't filtered by environment.",
    ],
    [
      `${P}/traces/abc`,
      "A trace is shown whole. The range filters lists.",
      "A trace is shown whole.",
    ],
    [
      `${P}/traces/compare`,
      "A trace is shown whole. The range filters lists.",
      "A trace is shown whole.",
    ],
    [`${P}/sessions/s`, "A session shows every turn, whatever the range.", undefined],
    [
      `${P}/doctor`,
      "Findings aren't filtered by time; each one has its own window.",
      "Findings cover every environment.",
    ],
    [
      `${P}/doctor/i1`,
      "Findings aren't filtered by time; each one has its own window.",
      "Findings cover every environment.",
    ],
    [
      `${P}/alerts/r1`,
      "Each rule watches its own window.",
      "Rules set their own environment filter.",
    ],
    [`${P}/budgets`, "Budgets track their own period.", "Budgets set their own scope."],
    [
      `${P}/datasets`,
      "Datasets aren't filtered by time.",
      "Datasets aren't filtered by environment.",
    ],
    [
      `${P}/experiments/x/runs/y`,
      "Experiments aren't filtered by time.",
      "Experiments aren't filtered by environment.",
    ],
    [
      `${P}/queues`,
      "Review queues aren't filtered by time.",
      "Review queues aren't filtered by environment.",
    ],
    [
      `${P}/prompts/support`,
      "Prompts aren't filtered by time.",
      "Prompts aren't filtered by environment.",
    ],
    [
      `${P}/settings/project`,
      "Settings aren't filtered by time.",
      "Settings aren't filtered by environment.",
    ],
    [
      `${P}/settings/billing-sources/s`,
      "Reconciliation picks its own days.",
      "Settings aren't filtered by environment.",
    ],
  ])("%s: range %s, environment %s", (path, range, environment) => {
    const options = dataFiltersFor(path);
    expect(options.rangeDisabled).toBe(range);
    expect(options.environmentDisabled).toBe(environment);
  });

  it("offers the gateway overview only the windows its endpoint accepts", () => {
    const options = dataFiltersFor(`${P}/gateway`);
    expect(options.presets).toEqual(["1h", "24h", "7d"]);
    expect(options.allowCustom).toBe(false);
    expect(options.fallback).toBe("7d");
    expect(options.presetNote).toBe("The gateway overview covers at most 7 days.");
    expect(options.rangeDisabled).toBeUndefined();
    expect(options.environmentDisabled).toBeUndefined();
  });
});

describe("hasEnabledFilter", () => {
  it("is false only when both controls are disabled", () => {
    expect(hasEnabledFilter(dataFiltersFor(`${P}/settings/project`))).toBe(false);
    expect(hasEnabledFilter(dataFiltersFor(`${P}/doctor`))).toBe(false);
    expect(hasEnabledFilter(dataFiltersFor(`${P}/sessions/s`))).toBe(true);
    expect(hasEnabledFilter(dataFiltersFor(`${P}/traces`))).toBe(true);
  });
});
