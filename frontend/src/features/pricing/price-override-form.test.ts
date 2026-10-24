import { describe, expect, it } from "vitest";

import {
  caseHint,
  emptyOverrideValues,
  overrideFormSchema,
  pricingSearchSchema,
  toOverrideInput,
  type OverrideFormValues,
} from "./price-override-form";

const VALID: OverrideFormValues = {
  provider: "openai",
  model_pattern: "ft:custom-v2",
  input_per_mtok: "1.50",
  output_per_mtok: "6",
  cached_input_per_mtok: "",
  cache_write_5m_per_mtok: "",
  cache_write_1h_per_mtok: "",
  effective_from: "2026-10-21",
};

function problems(values: Partial<OverrideFormValues>): Record<string, string> {
  const result = overrideFormSchema.safeParse({ ...VALID, ...values });
  return result.success
    ? {}
    : Object.fromEntries(result.error.issues.map((i) => [String(i.path[0]), i.message]));
}

describe("overrideFormSchema", () => {
  it("accepts a valid override", () => {
    expect(problems({})).toEqual({});
  });

  it("mirrors the server's bounds on provider and pattern", () => {
    expect(problems({ provider: " " })).toHaveProperty("provider");
    expect(problems({ provider: "a".repeat(100) })).toEqual({});
    expect(problems({ provider: "a".repeat(101) })).toHaveProperty("provider");
    expect(problems({ model_pattern: "m".repeat(200) })).toEqual({});
    expect(problems({ model_pattern: "m".repeat(201) })).toHaveProperty("model_pattern");
  });

  it("takes rates of 0 up to 999999.999999 with at most 6 decimals", () => {
    expect(problems({ input_per_mtok: "0" })).toEqual({});
    expect(problems({ input_per_mtok: ".5" })).toEqual({});
    expect(problems({ input_per_mtok: "999999.999999" })).toEqual({});
    expect(problems({ input_per_mtok: "1000000" })).toHaveProperty("input_per_mtok");
    expect(problems({ input_per_mtok: "0.0000001" })).toHaveProperty("input_per_mtok");
    expect(problems({ input_per_mtok: "-1" })).toHaveProperty("input_per_mtok");
    expect(problems({ output_per_mtok: "" })).toHaveProperty("output_per_mtok");
  });

  it("leaves the cached and cache-write rates optional but checks them when typed", () => {
    expect(problems({ cache_write_5m_per_mtok: "3.75" })).toEqual({});
    expect(problems({ cache_write_1h_per_mtok: "abc" })).toHaveProperty("cache_write_1h_per_mtok");
  });

  it("needs a real date", () => {
    expect(problems({ effective_from: "" })).toHaveProperty("effective_from");
    expect(problems({ effective_from: "2026-02-30" })).toHaveProperty("effective_from");
  });
});

describe("toOverrideInput", () => {
  it("sends trimmed text, null for blank optional rates and the day's UTC start", () => {
    expect(
      toOverrideInput({
        ...VALID,
        provider: " OpenAI ",
        input_per_mtok: "1.",
        cached_input_per_mtok: ".2",
      }),
    ).toEqual({
      provider: "OpenAI",
      model_pattern: "ft:custom-v2",
      input_per_mtok: "1",
      output_per_mtok: "6",
      cached_input_per_mtok: "0.2",
      cache_write_5m_per_mtok: null,
      cache_write_1h_per_mtok: null,
      effective_from: "2026-10-21T00:00:00Z",
    });
  });
});

describe("emptyOverrideValues", () => {
  it("prefills provider and pattern and starts today", () => {
    expect(
      emptyOverrideValues({ provider: "openai", model_pattern: "ft:custom-v2" }, "2026-10-24"),
    ).toMatchObject({
      provider: "openai",
      model_pattern: "ft:custom-v2",
      effective_from: "2026-10-24",
    });
  });
});

describe("pricingSearchSchema", () => {
  it("keeps provider (≤ 64) and model (≤ 256) and drops longer ones", () => {
    expect(pricingSearchSchema.parse({ provider: "openai", model: "ft:custom-v2" })).toMatchObject({
      provider: "openai",
      model: "ft:custom-v2",
    });
    const long = pricingSearchSchema.parse({ provider: "p".repeat(65), model: "m".repeat(257) });
    expect(long.provider).toBeUndefined();
    expect(long.model).toBeUndefined();
  });
});

describe("caseHint", () => {
  it("says a pattern with capitals is saved lower-case and won't match the original spelling", () => {
    expect(caseHint(" Meta-Llama-3 ")).toBe(
      "Saved as meta-llama-3. Model names match exactly, case included, so calls reporting Meta-Llama-3 won't use this price.",
    );
  });

  it("says nothing for a lower-case pattern", () => {
    expect(caseHint("ft:custom-v2")).toBeNull();
  });
});
