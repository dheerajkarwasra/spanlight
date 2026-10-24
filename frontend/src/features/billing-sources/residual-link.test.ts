import { describe, expect, it } from "vitest";

import type { ReconciliationGroup } from "@/lib/api";

import { residualTracesSearch } from "./residual-link";

function group(spanlight_models: string[], name = "gpt-4.1"): ReconciliationGroup {
  return { group: name, spanlight_models } as ReconciliationGroup;
}

describe("residualTracesSearch (spec ruling 7)", () => {
  it("opens the day's traces of the group's model when the project is in the report", () => {
    expect(residualTracesSearch("2026-10-14", group(["gpt-4.1"]), ["p0", "p1"], "p1")).toEqual({
      search: {
        range: "custom",
        from: "2026-10-14T00:00Z",
        to: "2026-10-15T00:00Z",
        model: "gpt-4.1",
      },
      extraModels: 0,
    });
  });

  it("ends the window at the next day's midnight across a month end", () => {
    const link = residualTracesSearch("2026-10-31", group(["gpt-4.1"]), ["p1"], "p1");
    expect(link?.search.to).toBe("2026-11-01T00:00Z");
  });

  it("filters by the first model and counts the others", () => {
    const link = residualTracesSearch("2026-10-14", group(["a", "b", "c"]), ["p1"], "p1");
    expect(link?.search.model).toBe("a");
    expect(link?.extraModels).toBe(2);
  });

  it("has no link when the project is not in the report", () => {
    expect(residualTracesSearch("2026-10-14", group(["gpt-4.1"]), ["p0"], "p1")).toBeNull();
  });

  it("has no link for an unmapped group without Spanlight models", () => {
    expect(
      residualTracesSearch("2026-10-14", group([], "unmapped:gpt-image-1"), ["p1"], "p1"),
    ).toBeNull();
  });
});
