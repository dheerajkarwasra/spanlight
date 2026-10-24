import { describe, expect, it } from "vitest";

import type { AlertRule, Budget, Insight } from "@/lib/api";

import { attentionItems, budgetRatio, projectBudget } from "./needs-attention";

const NOW = new Date("2026-10-21T12:00:00Z");

function insight(id: string, severity: Insight["severity"], title = id): Insight {
  return { id, severity, title, last_seen_at: "2026-10-21T11:00:00Z" } as Insight;
}

function rule(id: string, state: "ok" | "firing", enabled = true): AlertRule {
  return {
    id,
    name: `Rule ${id}`,
    enabled,
    state: { state, since: "2026-10-21T11:48:00Z", last_value: null, last_evaluated_at: null },
  } as AlertRule;
}

function budget(id: string, spent: string | null, amount = "500", overrides: Partial<Budget> = {}) {
  return {
    id,
    name: `Budget ${id}`,
    scope: "project",
    scope_id: null,
    period: "monthly",
    amount_usd: amount,
    enabled: true,
    state: { spent_usd: spent, state: "ok" },
    ...overrides,
  } as Budget;
}

describe("attentionItems", () => {
  it("lists findings by severity, then firing alerts, then budgets at 80 % or more", () => {
    const items = attentionItems({
      insights: [insight("w", "warning"), insight("c", "critical")],
      rules: [rule("ok", "ok"), rule("fire", "firing")],
      budgets: [budget("low", "100"), budget("high", "420")],
      now: NOW,
    });
    expect(items.map((item) => [item.kind, item.id, item.severity])).toEqual([
      ["finding", "c", "critical"],
      ["finding", "w", "warning"],
      ["alert", "fire", "critical"],
      ["budget", "high", "warning"],
    ]);
    expect(items[0]).toMatchObject({
      to: "/$orgId/$projectId/doctor/$insightId",
      params: { insightId: "c" },
    });
    expect(items[2]).toMatchObject({
      title: "Rule fire",
      meta: "Firing since 12 minutes ago",
      to: "/$orgId/$projectId/alerts/$ruleId",
      params: { ruleId: "fire" },
    });
    expect(items[3]).toMatchObject({
      meta: "84% of $500.00 · monthly",
      to: "/$orgId/$projectId/budgets",
    });
  });

  it("marks a spent budget critical and skips disabled or unmeasured ones", () => {
    const items = attentionItems({
      insights: [],
      rules: [rule("off", "firing", false)],
      budgets: [
        budget("over", "510"),
        budget("off", "499", "500", { enabled: false }),
        budget("unknown", null),
      ],
      now: NOW,
    });
    expect(items.map((item) => [item.id, item.severity])).toEqual([["over", "critical"]]);
  });

  it("keeps the three most severe findings, an older critical one ahead of newer warnings", () => {
    const items = attentionItems({
      insights: [
        insight("w1", "warning"),
        insight("w2", "warning"),
        insight("w3", "warning"),
        insight("old-critical", "critical"),
      ],
      rules: [],
      budgets: [],
      now: NOW,
    });
    expect(items.map((item) => item.id)).toEqual(["old-critical", "w1", "w2"]);
  });

  it("shows at most six", () => {
    const items = attentionItems({
      insights: [insight("a", "info"), insight("b", "info"), insight("c", "info")],
      rules: [rule("1", "firing"), rule("2", "firing"), rule("3", "firing")],
      budgets: [budget("x", "499")],
      now: NOW,
    });
    expect(items).toHaveLength(6);
  });
});

describe("project budget", () => {
  it("measures spent over amount, unknown when spend is", () => {
    expect(budgetRatio(budget("a", "125"))).toBe(0.25);
    expect(budgetRatio(budget("a", null))).toBeNull();
  });

  it("picks an enabled project-wide budget only", () => {
    expect(
      projectBudget([
        budget("model", "1", "5", { scope: "model", scope_id: "gpt" }),
        budget("off", "1", "5", { enabled: false }),
      ]),
    ).toBeNull();
    expect(projectBudget([budget("p", "1")])?.id).toBe("p");
  });
});
