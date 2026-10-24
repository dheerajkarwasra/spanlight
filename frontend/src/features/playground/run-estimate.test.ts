import { describe, expect, it } from "vitest";

import type { Credential, Price } from "@/lib/api";

import { newColumn, type ColumnState } from "./config-state";
import {
  estimateColumn,
  estimateForColumns,
  estimateRun,
  estimateTokens,
  formatEstimateUsd,
  pickPrice,
  runLabel,
} from "./run-estimate";

const AT = new Date("2026-10-21T12:00:00Z");

function price(
  provider: string,
  model_pattern: string,
  input: string,
  output: string,
  extra: Partial<Price> = {},
): Price {
  return {
    provider,
    model_pattern,
    input_per_mtok: input,
    output_per_mtok: output,
    cached_input_per_mtok: null,
    cache_write_5m_per_mtok: null,
    cache_write_1h_per_mtok: null,
    min_prompt_tokens: 0,
    effective_from: "2026-10-15T00:00:00Z",
    version: "2026-10-15",
    ...extra,
  };
}

// Rows of the backend's 2026-10-15 snapshot (backend/tests/pricing/test_snapshots.py).
const PRICES: Price[] = [
  price("openai", "gpt-4o", "2.50", "10.00"),
  price("openai", "gpt-4.1", "2.00", "8.00"),
  price("openai", "gpt-4.1-mini", "0.40", "1.60"),
  price("anthropic", "claude-haiku-4-5", "1.00", "5.00"),
  price("anthropic", "claude-haiku-5-5", "0.10", "0.50"),
  price("anthropic", "claude-haiku-5-5", "0.50", "2.50", { min_prompt_tokens: 100_001 }),
];

function messagesColumn(content: string, patch: Partial<ColumnState> = {}): ColumnState {
  return {
    ...newColumn("k"),
    sourceKind: "messages",
    messages: [{ role: "user", content }],
    ...patch,
  };
}

describe("estimateTokens", () => {
  // The cases of backend/tests/evals/test_reservation.py for app/pricing/tokens.py.
  it.each([
    ["", 0],
    ["abc", 1],
    ["abcd", 2],
    ["abcdéé", 4],
    ["日本語", 3],
  ])("estimates %j as %i tokens", (text, tokens) => {
    expect(estimateTokens(text)).toBe(tokens);
  });

  it("counts a character outside the BMP once, as Python's len does", () => {
    expect(estimateTokens("👍")).toBe(1);
  });
});

describe("pickPrice", () => {
  const pattern = (model: string, provider: string | null = "openai", tokens = 10) =>
    pickPrice(PRICES, provider, model, tokens, AT)?.model_pattern ?? null;

  it.each(["gpt-4o", "gpt-4o-20250101", "gpt-4o-latest", "gpt-4o-2024-08-06"])(
    "prices %s as gpt-4o (exact or snapshot suffix)",
    (model) => {
      expect(pattern(model)).toBe("gpt-4o");
    },
  );

  it.each(["gpt-4.1-nano", "gpt-4o-mini-x", "GPT-4o", " gpt-4o", "gpt-4o-latest\n", ""])(
    "does not price %j (never by prefix, case or whitespace)",
    (model) => {
      expect(pattern(model)).toBeNull();
    },
  );

  it("prices gpt-4.1-mini by its own row, not gpt-4.1", () => {
    expect(pattern("gpt-4.1-mini")).toBe("gpt-4.1-mini");
  });

  it("picks the highest tier whose min_prompt_tokens is not above the prompt", () => {
    const tier = (tokens: number) =>
      pickPrice(PRICES, "anthropic", "claude-haiku-5-5", tokens, AT)?.min_prompt_tokens;
    expect(tier(100_000)).toBe(0);
    expect(tier(100_001)).toBe(100_001);
  });

  it("skips rows not yet in force and takes the latest one in force", () => {
    const later = price("openai", "gpt-4o", "9.00", "9.00", {
      effective_from: "2026-11-01T00:00:00Z",
      version: "2026-11-01",
    });
    const prices = [...PRICES, later];
    expect(pickPrice(prices, "openai", "gpt-4o", 10, AT)?.version).toBe("2026-10-15");
    expect(pickPrice(prices, "openai", "gpt-4o", 10, new Date("2026-11-02"))?.version).toBe(
      "2026-11-01",
    );
  });

  it("requires the provider when given; none matches every row", () => {
    expect(pattern("gpt-4o", "anthropic")).toBeNull();
    expect(pattern("gpt-4o", "openai_compatible")).toBeNull();
    expect(pattern("gpt-4o", null)).toBe("gpt-4o");
  });
});

describe("estimateColumn", () => {
  it("counts input from the messages and output up to max tokens", () => {
    // 2 input tokens at $2.50 + 100 output tokens at $10.00 per million.
    const column = messagesColumn("abcdef", { model: "gpt-4o", maxTokens: "100" });
    expect(estimateColumn(column, "openai", PRICES, AT)).toEqual({ usd: "0.001005" });
  });

  it("counts a prompt version's compiled text", () => {
    const column: ColumnState = {
      ...newColumn("k"),
      version: {
        promptName: "greet",
        version: 1,
        id: "v1",
        variables: ["name"],
        template: { kind: "text", text: "Hello {{name}}" },
      },
      variables: { name: "Bob" },
      model: "gpt-4o",
      maxTokens: "1",
    };
    // "Hello Bob" is 9 ASCII characters: 3 tokens at $2.50, 1 output token at $10.00.
    expect(estimateColumn(column, "openai", PRICES, AT)).toEqual({ usd: "0.0000175" });
  });

  it("uses Anthropic's default of 1024 output tokens when max tokens is blank", () => {
    const column = messagesColumn("abc", { model: "claude-haiku-4-5" });
    // 1 token at $1.00 + 1024 tokens at $5.00 per million.
    expect(estimateColumn(column, "anthropic", PRICES, AT)).toEqual({ usd: "0.005121" });
  });

  it("has no ceiling for OpenAI with blank max tokens", () => {
    const column = messagesColumn("abc", { model: "gpt-4o" });
    expect(estimateColumn(column, "openai", PRICES, AT)).toEqual({
      unknown: "no_max_tokens",
      model: "gpt-4o",
    });
  });

  it("has no price for an unknown model, an OpenAI-compatible server or an unknown provider", () => {
    const column = messagesColumn("abc", { model: " my-model ", maxTokens: "10" });
    expect(estimateColumn(column, "openai", PRICES, AT)).toEqual({
      unknown: "no_price",
      model: "my-model",
    });
    const gpt = messagesColumn("abc", { model: "gpt-4o", maxTokens: "10" });
    expect(estimateColumn(gpt, "openai_compatible", PRICES, AT)).toMatchObject({
      unknown: "no_price",
    });
    expect(estimateColumn(gpt, null, PRICES, AT)).toMatchObject({ unknown: "no_price" });
  });
});

describe("estimateRun", () => {
  it("adds the columns up exactly", () => {
    const a = messagesColumn("abcdef", { model: "gpt-4o", maxTokens: "100" });
    const b = messagesColumn("abc", { model: "claude-haiku-4-5" });
    const run = estimateRun(
      [
        { column: a, provider: "openai" },
        { column: b, provider: "anthropic" },
      ],
      PRICES,
      AT,
    );
    expect(run).toEqual({ usd: "0.006126" });
  });

  it("names every column whose cost is unknown, never a partial sum", () => {
    const known = messagesColumn("abc", { model: "gpt-4o", maxTokens: "10" });
    const noCeiling = messagesColumn("abc", { model: "gpt-4o" });
    const noPrice = messagesColumn("abc", { model: "my-model", maxTokens: "10" });
    const run = estimateRun(
      [
        { column: known, provider: "openai" },
        { column: noCeiling, provider: "openai" },
        { column: noPrice, provider: "openai" },
      ],
      PRICES,
      AT,
    );
    expect(run).toEqual({
      unknown: [
        { column: 1, reason: "no_max_tokens", model: "gpt-4o" },
        { column: 2, reason: "no_price", model: "my-model" },
      ],
    });
  });
});

describe("formatEstimateUsd", () => {
  it.each([
    ["0.004", "$0.004"],
    ["0.001005", "$0.001005"],
    ["0.0012345", "$0.001235"],
    ["0.0012341", "$0.001235"],
    ["0.000001", "$0.000001"],
    ["0.1", "$0.10"],
    ["1.5", "$1.50"],
    ["0", "$0.00"],
  ])("formats %s as %s", (usd, text) => {
    expect(formatEstimateUsd(usd)).toBe(text);
  });

  it("floors a positive amount below a millionth of a dollar, never $0", () => {
    expect(formatEstimateUsd("0.0000004")).toBe("< $0.000001");
    expect(formatEstimateUsd("0.0000009")).toBe("< $0.000001");
  });
});

describe("estimateForColumns", () => {
  function credential(id: string, provider: Credential["provider"]): Credential {
    return {
      id,
      name: id,
      provider,
      base_url: null,
      created_by: null,
      created_at: "",
      last_used_at: null,
      last_checked_at: null,
      last_error: null,
      rotated_at: null,
    };
  }
  const column = messagesColumn("abc", { model: "gpt-4o", maxTokens: "10" });

  it("estimates once every column has a model and a resolved credential", () => {
    expect(estimateForColumns([column], [credential("c1", "openai")], PRICES, AT)).toEqual({
      usd: "0.0001025",
    });
  });

  it("shows nothing while credentials or prices load, never a false 'no price'", () => {
    expect(estimateForColumns([column], undefined, PRICES, AT)).toBeNull();
    expect(estimateForColumns([column], [credential("c1", "openai")], undefined, AT)).toBeNull();
  });

  it("shows nothing until a credential is picked among several, or when a column has no model", () => {
    const two = [credential("c1", "openai"), credential("c2", "anthropic")];
    expect(estimateForColumns([column], two, PRICES, AT)).toBeNull();
    expect(estimateForColumns([{ ...column, credentialId: "c1" }], two, PRICES, AT)).toEqual({
      usd: "0.0001025",
    });
    expect(estimateForColumns([{ ...column, credentialId: "gone" }], two, PRICES, AT)).toBeNull();
    const noModel = { ...column, model: " " };
    expect(estimateForColumns([noModel], [credential("c1", "openai")], PRICES, AT)).toBeNull();
  });
});

describe("runLabel", () => {
  it("says how many calls a run makes", () => {
    expect(runLabel(1)).toBe("Run");
    expect(runLabel(2)).toBe("Run 2 calls");
    expect(runLabel(3)).toBe("Run 3 calls");
  });
});
