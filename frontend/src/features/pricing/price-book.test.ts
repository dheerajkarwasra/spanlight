import { describe, expect, it } from "vitest";

import type { Price } from "@/lib/api";

import { filterBook, formatRate, latestByPattern } from "./price-book";

function price(overrides: Partial<Price>): Price {
  return {
    provider: "openai",
    model_pattern: "gpt-4.1",
    input_per_mtok: "2.000000",
    output_per_mtok: "8.000000",
    cached_input_per_mtok: null,
    cache_write_5m_per_mtok: null,
    cache_write_1h_per_mtok: null,
    min_prompt_tokens: 0,
    effective_from: "2026-04-14T00:00:00Z",
    version: "v9",
    ...overrides,
  };
}

describe("latestByPattern", () => {
  it("keeps the newest version of each provider and pattern, ordered by provider then pattern", () => {
    const rows = [
      price({ model_pattern: "gpt-4.1", effective_from: "2025-01-01T00:00:00Z", version: "v1" }),
      price({ model_pattern: "gpt-4.1", version: "v9" }),
      price({ provider: "anthropic", model_pattern: "claude-sonnet-4-5", version: "v14" }),
      price({ model_pattern: "gpt-4.1-mini", version: "v9" }),
      price({ provider: "anthropic", model_pattern: "claude-haiku-4-5", version: "v14" }),
    ];

    expect(
      latestByPattern(rows).map((row) => `${row.provider}/${row.model_pattern}@${row.version}`),
    ).toEqual([
      "anthropic/claude-haiku-4-5@v14",
      "anthropic/claude-sonnet-4-5@v14",
      "openai/gpt-4.1@v9",
      "openai/gpt-4.1-mini@v9",
    ]);
  });

  it("keeps every prompt tier of the newest version, base tier first", () => {
    const rows = [
      price({ provider: "google", model_pattern: "gemini-2.5-pro", min_prompt_tokens: 200_000 }),
      price({ provider: "google", model_pattern: "gemini-2.5-pro", min_prompt_tokens: 0 }),
      price({
        provider: "google",
        model_pattern: "gemini-2.5-pro",
        effective_from: "2024-01-01T00:00:00Z",
      }),
    ];

    expect(latestByPattern(rows).map((row) => row.min_prompt_tokens)).toEqual([0, 200_000]);
  });
});

describe("filterBook", () => {
  const rows = [
    price({ provider: "anthropic", model_pattern: "claude-sonnet-4-5" }),
    price({ model_pattern: "gpt-4.1-mini" }),
  ];

  it("matches the provider or the pattern, ignoring case and outer spaces", () => {
    expect(filterBook(rows, "  SONNET ").map((row) => row.model_pattern)).toEqual([
      "claude-sonnet-4-5",
    ]);
    expect(filterBook(rows, "OpenAI").map((row) => row.model_pattern)).toEqual(["gpt-4.1-mini"]);
  });

  it("returns every row for an empty search", () => {
    expect(filterBook(rows, " ")).toEqual(rows);
  });
});

describe("formatRate", () => {
  it.each([
    ["3.000000", "$3.00"],
    ["0.025000", "$0.025"],
    ["0.100000", "$0.10"],
    ["1234.5", "$1,234.50"],
    ["0", "$0.00"],
  ])("%s per 1M reads %s", (value, text) => {
    expect(formatRate(value)).toBe(text);
  });

  it("keeps an unknown rate null", () => {
    expect(formatRate(null)).toBeNull();
  });
});
