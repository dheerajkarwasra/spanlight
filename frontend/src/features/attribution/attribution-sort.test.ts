import { describe, expect, it } from "vitest";

import { ariaSortFor, SORT_COLUMNS, sortPhrase, withSort } from "./attribution-sort";

describe("attribution sort helpers", () => {
  it("lists the four sortable columns in the sheet's order", () => {
    expect(SORT_COLUMNS.map((column) => column.label)).toEqual([
      "Cost",
      "LLM calls",
      "Errors",
      "Retry cost",
    ]);
  });

  it("words each sort for the footer", () => {
    expect(sortPhrase("cost")).toBe("by cost");
    expect(sortPhrase("calls")).toBe("by LLM calls");
    expect(sortPhrase("errors")).toBe("by errors");
    expect(sortPhrase("retry_cost")).toBe("by retry cost");
  });

  it("keeps the group, key, window and environment when the sort changes", () => {
    const search = {
      group_by: "metadata" as const,
      key: "customer_id",
      sort: "cost" as const,
      range: "30d" as const,
      env: "prod",
    };
    expect(withSort(search, "errors")).toEqual({ ...search, sort: "errors" });
  });

  it("marks only the active column as sorted, always descending", () => {
    expect(ariaSortFor("cost", "cost")).toBe("descending");
    expect(ariaSortFor("calls", "cost")).toBe("none");
  });
});
