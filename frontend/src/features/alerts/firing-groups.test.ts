import { describe, expect, it } from "vitest";

import type { AlertEvent } from "@/lib/api";

import { groupFirings } from "./firing-groups";

function event(id: string, ruleId: string, startedAt: string, resolved = true): AlertEvent {
  return {
    id,
    rule_id: ruleId,
    rule_name: `Rule ${ruleId}`,
    rule_kind: ruleId === "b" ? "budget" : "threshold",
    state: resolved ? "resolved" : "firing",
    value: "1",
    threshold: "0.5",
    started_at: startedAt,
    resolved_at: resolved ? startedAt : null,
    acknowledged_by: null,
    acknowledged_at: null,
  };
}

describe("groupFirings", () => {
  it("folds one rule's firings into one row", () => {
    const events = [
      event("3", "r1", "2026-10-21T03:00:00Z", false),
      event("2", "r1", "2026-10-21T02:00:00Z"),
      event("1", "r1", "2026-10-21T01:00:00Z"),
    ];
    expect(groupFirings(events, false)).toEqual([
      {
        ruleId: "r1",
        ruleName: "Rule r1",
        ruleKind: "threshold",
        count: 3,
        countLabel: "3",
        lastAt: "2026-10-21T03:00:00Z",
        firing: true,
        events,
      },
    ]);
  });

  it("orders interleaved rules by their latest firing", () => {
    const events = [
      event("4", "r2", "2026-10-21T04:00:00Z"),
      event("3", "b", "2026-10-21T03:00:00Z"),
      event("2", "r2", "2026-10-21T02:00:00Z"),
      event("1", "r1", "2026-10-21T01:00:00Z"),
    ];
    const groups = groupFirings(events, false);
    expect(groups.map((group) => [group.ruleId, group.count, group.firing])).toEqual([
      ["r2", 2, false],
      ["b", 1, false],
      ["r1", 1, false],
    ]);
    expect(groups[1]?.ruleKind).toBe("budget");
    expect(groups[0]?.events.map((item) => item.id)).toEqual(["4", "2"]);
  });

  it("marks every count open-ended while older pages exist", () => {
    const events = [
      event("3", "r2", "2026-10-21T03:00:00Z"),
      event("2", "r1", "2026-10-21T02:00:00Z"),
      event("1", "r1", "2026-10-21T01:00:00Z"),
    ];
    const labels = (hasMore: boolean) =>
      groupFirings(events, hasMore).map((group) => [group.ruleId, group.countLabel]);
    expect(labels(true)).toEqual([
      ["r2", "1+"],
      ["r1", "2+"],
    ]);
    expect(labels(false)).toEqual([
      ["r2", "1"],
      ["r1", "2"],
    ]);
  });
});
