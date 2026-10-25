import { describe, expect, it } from "vitest";

import type { Activation } from "@/lib/api";

import { ACTIVATION_STEPS, checklistVisible, doneCount } from "./activation-steps";

const HALF: Activation = { first_trace: true, session: true, budget: false, evaluation: false };
const ALL: Activation = { first_trace: true, session: true, budget: true, evaluation: true };

describe("ACTIVATION_STEPS", () => {
  it("lists the four steps in the spec's order", () => {
    expect(ACTIVATION_STEPS.map((step) => step.id)).toEqual([
      "first_trace",
      "session",
      "budget",
      "evaluation",
    ]);
    expect(ACTIVATION_STEPS.map((step) => step.label)).toEqual([
      "Send a trace from your app",
      "Group traces into a session",
      "Set a budget",
      "Run an eval or a review",
    ]);
  });

  it("gates only the budget link behind the permission that creates budgets", () => {
    const gated = ACTIVATION_STEPS.flatMap((step) =>
      step.links.filter((link) => "requires" in link && link.requires),
    );
    expect(gated).toEqual([
      {
        label: "Open Budgets",
        to: "/$orgId/$projectId/budgets",
        requires: "alerts:write",
        otherwise: "Ask an admin to set one.",
      },
    ]);
  });
});

describe("doneCount", () => {
  it("counts the finished steps", () => {
    expect(doneCount(HALF)).toBe(2);
    expect(doneCount(ALL)).toBe(4);
  });
});

describe("checklistVisible", () => {
  const visible = { hasTraces: true, isDemoOrg: false, dismissed: false, activation: HALF };

  it("shows once a trace exists, outside the demo, undismissed and unfinished", () => {
    expect(checklistVisible(visible)).toBe(true);
  });

  it.each([
    ["before the first trace", { hasTraces: false }],
    ["while traces are unknown", { hasTraces: undefined }],
    ["in the demo org", { isDemoOrg: true }],
    ["once dismissed", { dismissed: true }],
    ["when all four steps are done", { activation: ALL }],
    ["while the steps are unknown", { activation: undefined }],
  ])("hides %s", (_, override) => {
    expect(checklistVisible({ ...visible, ...override })).toBe(false);
  });
});
