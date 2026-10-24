import { describe, expect, it } from "vitest";

import { attributionSearchSchema } from "./search";

describe("attributionSearchSchema", () => {
  it("defaults to trace names sorted by cost", () => {
    expect(attributionSearchSchema.parse({})).toEqual({ group_by: "trace_name", sort: "cost" });
  });

  it("falls back to the defaults for an invalid group_by or sort", () => {
    expect(attributionSearchSchema.parse({ group_by: "team", sort: "latency" })).toEqual({
      group_by: "trace_name",
      sort: "cost",
    });
  });

  it("keeps the key only with metadata", () => {
    expect(attributionSearchSchema.parse({ group_by: "metadata", key: "a.b-c" })).toMatchObject({
      group_by: "metadata",
      key: "a.b-c",
    });
    expect(attributionSearchSchema.parse({ group_by: "user", key: "a.b-c" }).key).toBeUndefined();
  });

  it("keeps a chart focus up to 512 characters", () => {
    expect(attributionSearchSchema.parse({ focus: "support-bot" }).focus).toBe("support-bot");
    expect(attributionSearchSchema.parse({ focus: "x".repeat(513) }).focus).toBeUndefined();
    expect(attributionSearchSchema.parse({ focus: "" }).focus).toBeUndefined();
    // The router reads `focus=42` as a number; a release or trace name can be all digits.
    expect(attributionSearchSchema.parse({ focus: 42 }).focus).toBe("42");
  });

  it("keeps the shell's window and environment params", () => {
    expect(
      attributionSearchSchema.parse({ group_by: "release", range: "7d", env: "prod" }),
    ).toEqual({ group_by: "release", sort: "cost", range: "7d", env: "prod" });
  });
});
