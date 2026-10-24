import { describe, expect, it } from "vitest";

import { resolveTab, type InspectorTab, type TabSpan } from "./inspector-tabs";

const NO_USAGE = {
  model: null,
  provider: null,
  input_tokens: null,
  output_tokens: null,
  cached_tokens: null,
  cache_write_tokens: null,
  cost_usd: null,
};
const CHAIN: TabSpan = { kind: "chain", ...NO_USAGE };
const LLM: TabSpan = { kind: "llm", ...NO_USAGE, model: "gpt-4.1" };
/** A chain span carrying usage (an SDK span rolled up over the gateway's call). */
const CHAIN_WITH_USAGE: TabSpan = { kind: "chain", ...NO_USAGE, input_tokens: 12 };

describe("resolveTab", () => {
  it.each<[string | undefined, TabSpan, InspectorTab]>([
    [undefined, LLM, "messages"],
    ["messages", CHAIN, "messages"],
    ["scores", CHAIN, "scores"],
    ["attributes", CHAIN, "attributes"],
    ["raw", CHAIN, "raw"],
    ["cost", LLM, "cost"],
    ["replays", LLM, "replays"],
    // A tab that doesn't apply to the span falls back to Messages.
    ["cost", CHAIN, "messages"],
    ["replays", CHAIN, "messages"],
    ["replays", CHAIN_WITH_USAGE, "messages"],
    // Cost follows the usage the span carries, whatever its kind.
    ["cost", CHAIN_WITH_USAGE, "cost"],
    // Old internal values from links made before the rework.
    ["io", CHAIN, "messages"],
    ["metadata", CHAIN, "attributes"],
    // Anything else.
    ["bogus", LLM, "messages"],
    ["", LLM, "messages"],
    ["COST", LLM, "messages"],
    ["toString", LLM, "messages"],
  ])("tab=%s on a %o span shows %s", (tab, span, expected) => {
    expect(resolveTab(tab, span)).toBe(expected);
  });
});
