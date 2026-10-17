import { describe, expect, it } from "vitest";

import { traceFiltersSchema } from "./search";
import { toTraceListQuery } from "./trace-queries";
import {
  isMetadataValueTooLong,
  metadataFilterLabel,
  metadataKeyOptions,
  promotedPairs,
  sortedPairs,
} from "./metadata-filter";

describe("metadataFilterLabel", () => {
  it("reads key = value, or No key for the key alone", () => {
    expect(metadataFilterLabel("customer_id", "acme")).toBe("customer_id = acme");
    expect(metadataFilterLabel("customer_id", undefined)).toBe("No customer_id");
  });
});

describe("metadataKeyOptions", () => {
  it("puts the promoted keys first, then the seen keys not already listed", () => {
    expect(metadataKeyOptions(["plan", "customer_id"], ["customer_id", "region", "plan"])).toEqual([
      "plan",
      "customer_id",
      "region",
    ]);
  });

  it("keeps a key from the URL that neither list has", () => {
    expect(metadataKeyOptions(["plan"], [], "a.b-c")).toEqual(["a.b-c", "plan"]);
    expect(metadataKeyOptions(["plan"], [], "plan")).toEqual(["plan"]);
  });
});

describe("promotedPairs", () => {
  it("keeps only the promoted keys the trace carries, in promoted order", () => {
    const metadata = { region: "eu", customer_id: "acme", feature: "triage" };
    expect(promotedPairs(metadata, ["feature", "plan", "customer_id"])).toEqual([
      ["feature", "triage"],
      ["customer_id", "acme"],
    ]);
    expect(promotedPairs(metadata, [])).toEqual([]);
  });

  it("ignores inherited object keys: constructor and __proto__ are valid promoted keys", () => {
    expect(promotedPairs({ region: "eu" }, ["constructor", "__proto__", "region"])).toEqual([
      ["region", "eu"],
    ]);
    // A trace that really carries them, parsed from JSON as the API client does.
    const carried = JSON.parse('{"constructor": "c", "__proto__": "p"}') as Record<string, string>;
    expect(promotedPairs(carried, ["constructor", "__proto__"])).toEqual([
      ["constructor", "c"],
      ["__proto__", "p"],
    ]);
  });
});

describe("sortedPairs", () => {
  it("lists every pair by key", () => {
    expect(sortedPairs({ region: "eu", customer_id: "acme", "a.b-c": "x" })).toEqual([
      ["a.b-c", "x"],
      ["customer_id", "acme"],
      ["region", "eu"],
    ]);
  });
});

describe("the metadata value limit", () => {
  it("counts code points, not UTF-16 units", () => {
    expect(isMetadataValueTooLong("😀".repeat(256))).toBe(false);
    expect(isMetadataValueTooLong("😀".repeat(257))).toBe(true);
  });
});

describe("the metadata search params", () => {
  it("accepts a key with a dot and a dash, and drops an invalid key", () => {
    expect(traceFiltersSchema.parse({ metadata_key: "a.b-c" }).metadata_key).toBe("a.b-c");
    expect(traceFiltersSchema.parse({ metadata_key: "Bad Key" }).metadata_key).toBeUndefined();
  });

  it("drops an empty value, so the key alone is the missing-key filter", () => {
    expect(
      traceFiltersSchema.parse({ metadata_key: "k", metadata_value: "" }).metadata_value,
    ).toBeUndefined();
  });

  it("accepts a 256-code-point value and drops a longer one", () => {
    const ok = "é".repeat(256);
    expect(traceFiltersSchema.parse({ metadata_value: ok }).metadata_value).toBe(ok);
    expect(
      traceFiltersSchema.parse({ metadata_value: "é".repeat(257) }).metadata_value,
    ).toBeUndefined();
  });

  it("accepts the attribution drill-down params", () => {
    const parsed = traceFiltersSchema.parse({
      range: "custom",
      from: "2026-09-16T00:00:00Z",
      to: "2026-10-16T00:00:00Z",
      env: "production",
      metadata_key: "customer_id",
      metadata_value: "[REDACTED]",
      name: "summarize",
      user: "u_1",
      release: "v2",
    });
    expect(parsed).toMatchObject({
      metadata_key: "customer_id",
      metadata_value: "[REDACTED]",
      name: "summarize",
      user: "u_1",
      release: "v2",
    });
  });
});

describe("the list query", () => {
  const window = { from: "2026-10-16T00:00:00Z", to: "2026-10-17T00:00:00Z" };

  it("sends the pair, the key alone, and never a value alone", () => {
    const pair = toTraceListQuery(
      { metadata_key: "a.b-c", metadata_value: "x" },
      window,
      undefined,
    );
    expect(pair).toMatchObject({ metadata_key: "a.b-c", metadata_value: "x" });

    const keyOnly = toTraceListQuery({ metadata_key: "a.b-c" }, window, undefined);
    expect(keyOnly).toMatchObject({ metadata_key: "a.b-c", metadata_value: undefined });

    const valueOnly = toTraceListQuery({ metadata_value: "x" }, window, undefined);
    expect(valueOnly).toMatchObject({ metadata_key: undefined, metadata_value: undefined });
  });

  it("sends the exact trace name", () => {
    expect(toTraceListQuery({ name: "summarize" }, window, undefined).name).toBe("summarize");
  });
});
