import { describe, expect, it } from "vitest";

import { NO_PRICE_REASON } from "@/components/cost-value";

import { costUnknownReason, TOKENS_NOT_REPORTED, tokenUnknownReason } from "./usage-reasons";

const unpriced = {
  model: "claude-sonnet-4-5",
  input_tokens: 1312,
  output_tokens: 528,
  attributes: {},
  usage_state: "no_price" as const,
};

const outputMissing = { ...unpriced, output_tokens: null, usage_state: "partial_usage" as const };

describe("costUnknownReason", () => {
  it("names the cut stream when the output count never came", () => {
    const span = {
      ...outputMissing,
      attributes: { "usage.unreported_reason": "client_disconnected" },
    };
    expect(costUnknownReason(span)).toBe(
      "Unknown: output tokens not reported (the client disconnected)",
    );
  });

  it("words every stored cut-stream reason as the server does", () => {
    const reasons: Record<string, string> = {
      client_disconnected: "the client disconnected",
      stream_error: "the stream failed",
      stream_cut_by_fault: "a Lab fault cut the stream",
      include_usage_not_set: "stream_options.include_usage was not set",
      no_final_usage: "the provider sent no final usage event",
    };
    for (const [reason, sentence] of Object.entries(reasons)) {
      const span = { ...outputMissing, attributes: { "usage.unreported_reason": reason } };
      expect(tokenUnknownReason(span, "output")).toBe(`Not reported: ${sentence}`);
    }
  });

  it("uses the usage state copy when no reason was stored", () => {
    expect(costUnknownReason(outputMissing)).toBe("Usage is incomplete, so no cost was computed");
    expect(tokenUnknownReason(outputMissing, "output")).toBe("The exporter sent only input tokens");
  });

  it("words an exporter that sent no usage by the usage state", () => {
    const span = {
      ...unpriced,
      input_tokens: null,
      output_tokens: null,
      usage_state: "usage_not_reported" as const,
      attributes: { "usage.unreported_reason": "not_exported" },
    };
    expect(tokenUnknownReason(span, "input")).toBe("The exporter sent no token counts");
    expect(costUnknownReason(span)).toBe("The exporter sent no token counts");
  });

  it("ignores a reason value it does not know", () => {
    const span = { ...outputMissing, attributes: { "usage.unreported_reason": 7 } };
    expect(tokenUnknownReason(span, "output")).toBe("The exporter sent only input tokens");
    const inherited = { ...span, attributes: { "usage.unreported_reason": "constructor" } };
    expect(tokenUnknownReason(inherited, "output")).toBe("The exporter sent only input tokens");
  });

  it("falls back to neutral copy when the state explains nothing", () => {
    const rollup = { ...outputMissing, usage_state: "rollup" as const };
    expect(tokenUnknownReason(rollup, "output")).toBe(TOKENS_NOT_REPORTED);
    expect(costUnknownReason(rollup)).toBe("Unknown: output tokens not reported for this call");
    expect(TOKENS_NOT_REPORTED).not.toMatch(/SDK|gateway/);
  });

  it("blames the price only when the usage is known", () => {
    expect(costUnknownReason(unpriced)).toBe(NO_PRICE_REASON);
  });

  it("says there was no model call on a span that is not one", () => {
    expect(
      costUnknownReason({ ...unpriced, model: null, usage_state: "not_applicable" as const }),
    ).toBe("No model call on this span");
  });
});
