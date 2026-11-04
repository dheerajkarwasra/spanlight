import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { resolveRange } from "@/lib/time-range";

import { carryForward } from "./live-window";

describe("carryForward", () => {
  it("moves cached preset windows to the next minute, stale, and leaves other queries alone", () => {
    const client = new QueryClient();
    const prev = new Date("2026-11-04T10:00:30Z");
    const next = new Date("2026-11-04T10:01:10Z");
    const before = resolveRange({ range: "24h" }, prev);
    const after = resolveRange({ range: "24h" }, next);
    const custom = [
      "project",
      "p1",
      "overview",
      { from: "2026-11-01T00:00:00.000Z", to: "2026-11-02T00:00:00.000Z" },
    ];
    client.setQueryData(["project", "p1", "overview", { from: before.from, to: before.to }], {
      traces: 7,
    });
    client.setQueryData(custom, { traces: 3 });

    carryForward(client, prev, next);

    const movedKey = ["project", "p1", "overview", { from: after.from, to: after.to }];
    expect(client.getQueryData(movedKey)).toEqual({ traces: 7 });
    expect(client.getQueryState(movedKey)?.dataUpdatedAt).toBe(0);
    expect(client.getQueryCache().getAll()).toHaveLength(3);
  });
});
