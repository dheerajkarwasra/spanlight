import { describe, expect, it } from "vitest";

import { budgetsSearchSchema, prefillFromSearch } from "./budget-prefill";

describe("prefillFromSearch", () => {
  it.each([
    [
      { scope: "trace_name", value: "summarize" },
      { scope: "trace_name", traceName: "summarize" },
    ],
    [
      { scope: "user", value: "acme-corp" },
      { scope: "user", userId: "acme-corp" },
    ],
    [
      { scope: "model", value: "gpt-4.1" },
      { scope: "model", model: "gpt-4.1" },
    ],
    [
      { scope: "metadata", key: "customer_id", value: "acme" },
      { scope: "metadata", metadataKey: "customer_id", metadataValue: "acme" },
    ],
  ] as const)("prefills %o", (search, expected) => {
    expect(prefillFromSearch({ new: "1", ...search })).toEqual(expected);
  });

  it("opens a blank dialog for metadata without a key, or a scope without a value", () => {
    expect(prefillFromSearch({ new: "1", scope: "metadata", value: "acme" })).toEqual({});
    expect(prefillFromSearch({ new: "1", scope: "user" })).toEqual({});
    expect(prefillFromSearch({ new: "1" })).toEqual({});
  });

  it("does nothing without new=1", () => {
    expect(prefillFromSearch({ scope: "user", value: "acme-corp" })).toBeNull();
  });
});

describe("budgetsSearchSchema", () => {
  it("reads values the router parsed as numbers back as text", () => {
    expect(budgetsSearchSchema.parse({ new: 1, scope: "user", value: 12345 })).toEqual({
      new: "1",
      scope: "user",
      value: "12345",
    });
  });

  it("drops unreadable params instead of failing the route", () => {
    expect(
      budgetsSearchSchema.parse({ new: "2", scope: "release", value: "x".repeat(513), key: "k" }),
    ).toEqual({ key: "k" });
  });
});
