import { describe, expect, it } from "vitest";

import { budgetSearchFor } from "./budget-link";

describe("budgetSearchFor", () => {
  it.each([
    ["trace_name", null, "summarize", { new: "1", scope: "trace_name", value: "summarize" }],
    ["user", null, "u-42", { new: "1", scope: "user", value: "u-42" }],
    [
      "metadata",
      "customer_id",
      "acme",
      { new: "1", scope: "metadata", key: "customer_id", value: "acme" },
    ],
  ] as const)("prefills a %s budget", (groupBy, key, value, expected) => {
    expect(budgetSearchFor(groupBy, key, value)).toEqual(expected);
  });

  it("offers none for a release, which budgets can't scope", () => {
    expect(budgetSearchFor("release", null, "v1.4.0")).toBeNull();
  });

  it("offers none for metadata without a key", () => {
    expect(budgetSearchFor("metadata", null, "acme")).toBeNull();
  });
});
