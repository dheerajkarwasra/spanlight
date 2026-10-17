import { describe, expect, it } from "vitest";

import { exportFilterEntries, toExportFilters } from "./export-filters";

const WINDOW = { from: "2026-10-16T00:00:00Z", to: "2026-10-17T00:00:00Z" };

describe("toExportFilters", () => {
  it("sends the metadata pair as the trace list applies it, value untrimmed", () => {
    const filters = toExportFilters(WINDOW, undefined, {
      metadata_key: "a.b-c",
      metadata_value: " acme",
    });
    expect(filters).toEqual({ ...WINDOW, metadata_key: "a.b-c", metadata_value: " acme" });
    expect(exportFilterEntries(filters)).toEqual([
      { key: "metadata_key", value: "a.b-c" },
      { key: "metadata_value", value: " acme" },
    ]);
  });

  it("sends the key alone for the missing-key filter, and never a value alone", () => {
    expect(toExportFilters(WINDOW, undefined, { metadata_key: "customer_id" })).toEqual({
      ...WINDOW,
      metadata_key: "customer_id",
    });
    expect(toExportFilters(WINDOW, undefined, { metadata_value: "acme" })).toEqual(WINDOW);
  });
});
