import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { PlaygroundResult, PlaygroundUsage } from "@/lib/api";

import { NO_OUTPUT_COUNT_REASON, ResultMetrics } from "./result-metrics";

const usage: PlaygroundUsage = {
  input_tokens: 12,
  output_tokens: null,
  cached_tokens: null,
  cache_write_tokens: null,
  cache_write_1h_tokens: null,
};

const result: PlaygroundResult = {
  output: "Hello",
  usage,
  cost_usd: null,
  latency_ms: 840,
  ttft_ms: null,
  trace_id: null,
  span_id: null,
  error: null,
};

function tokensValue(): HTMLElement {
  const value = screen.getByText("Tokens").nextElementSibling;
  if (!(value instanceof HTMLElement)) {
    throw new Error("no tokens value");
  }
  return value;
}

describe("ResultMetrics tokens", () => {
  it("shows an unreported output count as an explained dash, never a question mark", () => {
    render(
      <TooltipProvider>
        <ResultMetrics result={result} />
      </TooltipProvider>,
    );
    expect(tokensValue()).toHaveTextContent("12 in · — out");
    expect(tokensValue()).not.toHaveTextContent("?");
    expect(screen.getByLabelText(`Unknown: ${NO_OUTPUT_COUNT_REASON}`)).toHaveTextContent("—");
  });

  it("shows both counts when both are known", () => {
    render(
      <TooltipProvider>
        <ResultMetrics result={{ ...result, usage: { ...usage, output_tokens: 1200 } }} />
      </TooltipProvider>,
    );
    expect(tokensValue()).toHaveTextContent("12 in · 1,200 out");
  });
});
