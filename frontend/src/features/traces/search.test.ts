import { describe, expect, it } from "vitest";

import { traceFiltersSchema } from "./search";

describe("traceFiltersSchema prompt_version", () => {
  it("keeps a version ID and drops anything else, so the API never gets a bad one", () => {
    const id = "0192f3a4-5b6c-7d8e-9f01-23456789abcd";
    expect(traceFiltersSchema.parse({ prompt_version: id }).prompt_version).toBe(id);
    expect(traceFiltersSchema.parse({ prompt_version: "v3" }).prompt_version).toBeUndefined();
  });
});

describe("traceFiltersSchema sort and order", () => {
  it("takes every list sort with a direction and drops an unknown direction", () => {
    expect(traceFiltersSchema.parse({ sort: "cost", order: "asc" })).toMatchObject({
      sort: "cost",
      order: "asc",
    });
    expect(traceFiltersSchema.parse({ sort: "duration", order: "sideways" }).order).toBeUndefined();
    expect(traceFiltersSchema.parse({ sort: "sideways" }).sort).toBeUndefined();
  });

  it("keeps an old retry-cost link without a direction", () => {
    const search = traceFiltersSchema.parse({ sort: "retry_cost_usd" });
    expect(search.sort).toBe("retry_cost_usd");
    expect(search.order).toBeUndefined();
  });
});
