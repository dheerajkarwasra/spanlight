import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Span } from "@/lib/api";

import { SpanUsageCard } from "./span-usage-card";

const base: Span = {
  span_id: "span_1",
  parent_span_id: null,
  kind: "llm",
  name: "chat claude-sonnet-4-5",
  status: "ok",
  status_message: null,
  error_class: null,
  provider_error_type: null,
  provider_error_code: null,
  rate_limit_kind: null,
  started_at: "2026-10-15T10:00:00Z",
  ended_at: "2026-10-15T10:00:01Z",
  duration_ms: 1000,
  provider: "anthropic",
  model: "claude-sonnet-4-5",
  input_tokens: 1312,
  output_tokens: 528,
  cached_tokens: 896,
  cache_write_tokens: null,
  cache_write_1h_tokens: null,
  service_tier: "standard",
  web_search_requests: null,
  cost_usd: "0.0164",
  fee_usd: null,
  pricing_version: "2026-10-01",
  time_to_first_token_ms: 412,
  input: null,
  output: null,
  attributes: {},
  truncated: false,
  payload_omitted: false,
  finish_reason: null,
  prompt_version_id: null,
  usage_rollup: false,
  usage_state: "priced",
};

function renderCard(overrides: Partial<Span>) {
  render(
    <TooltipProvider>
      <SpanUsageCard span={{ ...base, ...overrides }} />
    </TooltipProvider>,
  );
}

describe("SpanUsageCard cache writes", () => {
  it("hides the row when cache writes were not reported", () => {
    renderCard({ cache_write_tokens: null, cache_write_1h_tokens: null });
    expect(screen.queryByText("Cache writes")).not.toBeInTheDocument();
    expect(screen.getByText("Cached tokens")).toBeInTheDocument();
  });

  it("shows the count and the 1 h part for the pilot case", () => {
    renderCard({ cache_write_tokens: 6404, cache_write_1h_tokens: 6404 });
    expect(screen.getByText("Cache writes")).toBeInTheDocument();
    expect(screen.getByText("6,404")).toBeInTheDocument();
    expect(screen.getByText("· 1 h: 6,404")).toHaveAttribute("aria-hidden");
    expect(screen.getByText(", 1-hour cache: 6,404")).toHaveClass("sr-only");
  });

  it("wraps the count and the 1 h part at narrow widths instead of clipping them", () => {
    renderCard({ cache_write_tokens: 6404, cache_write_1h_tokens: 6404 });
    const value = screen.getByText("· 1 h: 6,404").closest("dd")?.firstElementChild;
    expect(value).toHaveClass("break-words");
    expect(value).not.toHaveClass("truncate");
    // The other fields keep the single-line ellipsis.
    const cached = screen.getByText("896").closest("dd")?.firstElementChild;
    expect(cached).toHaveClass("truncate");
  });

  it("shows only the count when no 1 h part was reported", () => {
    renderCard({ cache_write_tokens: 6404, cache_write_1h_tokens: null });
    expect(screen.getByText("6,404")).toBeInTheDocument();
    expect(screen.queryByText(/1 h:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/1-hour cache/)).not.toBeInTheDocument();
  });

  it("shows only the count when the 1 h part is zero", () => {
    renderCard({ cache_write_tokens: 6404, cache_write_1h_tokens: 0 });
    expect(screen.getByText("6,404")).toBeInTheDocument();
    expect(screen.queryByText(/1 h:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/1-hour cache/)).not.toBeInTheDocument();
  });
});

describe("SpanUsageCard service tier and web searches", () => {
  function fieldValue(label: string): HTMLElement {
    const term = screen.getByText(label, { selector: "dt" });
    const value = term.nextElementSibling;
    if (!(value instanceof HTMLElement)) {
      throw new Error(`no value for ${label}`);
    }
    return value;
  }

  it("shows the tier and the search count when reported", () => {
    renderCard({ service_tier: "batch", web_search_requests: 3 });
    expect(fieldValue("Service tier")).toHaveTextContent("batch");
    expect(fieldValue("Web searches")).toHaveTextContent("3");
  });

  it("shows zero searches as a count, not as unknown", () => {
    renderCard({ web_search_requests: 0 });
    expect(fieldValue("Web searches")).toHaveTextContent("0");
  });

  it("marks both as not reported when the span predates them", () => {
    renderCard({ service_tier: null, web_search_requests: null });
    for (const label of ["Service tier", "Web searches"]) {
      expect(
        within(fieldValue(label)).getByLabelText(
          "Unknown: Not reported by the provider for this call.",
        ),
      ).toHaveTextContent("—");
    }
  });

  it("places the two fields between cache writes and cost", () => {
    renderCard({ cache_write_tokens: 6404, cache_write_1h_tokens: 6404 });
    const labels = Array.from(document.querySelectorAll("dt")).map((term) => term.textContent);
    expect(labels).toEqual([
      "Model",
      "Provider",
      "Input tokens",
      "Output tokens",
      "Cached tokens",
      "Cache writes",
      "Service tier",
      "Web searches",
      "Cost",
      "Time to first token",
      "Pricing version",
    ]);
  });
});

describe("SpanUsageCard unknown reasons", () => {
  function unknownLabel(label: string): string | null {
    const term = screen.getByText(label, { selector: "dt" });
    const value = term.nextElementSibling;
    if (!(value instanceof HTMLElement)) {
      throw new Error(`no value for ${label}`);
    }
    return within(value).getByText("—").getAttribute("aria-label");
  }

  it("gives a cut stream's reason, not a missing price", () => {
    renderCard({
      name: "gateway anthropic.messages",
      output_tokens: null,
      cost_usd: null,
      pricing_version: null,
      usage_state: "partial_usage",
      attributes: { "usage.unreported_reason": "client_disconnected", "spanlight.gateway": true },
    });
    expect(unknownLabel("Cost")).toBe(
      "Unknown: output tokens not reported (the client disconnected)",
    );
    expect(unknownLabel("Output tokens")).toBe("Not reported: the client disconnected");
    expect(document.body.textContent).not.toMatch(/by the SDK/);
  });

  it("blames the price only when both token counts are known", () => {
    renderCard({ cost_usd: null, pricing_version: null, usage_state: "no_price" });
    expect(unknownLabel("Cost")).toBe("Unknown: No price for this model");
  });

  it("does not credit the SDK with a count nobody reported", () => {
    renderCard({ cached_tokens: null });
    expect(unknownLabel("Cached tokens")).toBe("Unknown: Not reported for this call");
  });
});

describe("SpanUsageCard usage states", () => {
  function unknownLabel(label: string): string | null {
    const term = screen.getByText(label, { selector: "dt" });
    const value = term.nextElementSibling;
    if (!(value instanceof HTMLElement)) {
      throw new Error(`no value for ${label}`);
    }
    return within(value).getByText("—").getAttribute("aria-label");
  }

  it("shows the rollup badge and the tokens as sent", () => {
    renderCard({ name: "invoke_agent support-copilot", usage_rollup: true, usage_state: "rollup" });
    const badge = screen.getByText("Counted on child spans");
    expect(badge.tagName).toBe("SPAN");
    expect(badge).not.toHaveAttribute("role");
    expect(badge).not.toHaveAttribute("aria-live");
    expect(screen.getByText("1,312")).toBeInTheDocument();
    expect(screen.getByText("528")).toBeInTheDocument();
    expect(screen.getByText("$0.0164")).toBeInTheDocument();
  });

  it("shows dashes with the not-reported reason", () => {
    renderCard({
      name: "chat gpt-4.1",
      provider: "openai",
      model: "gpt-4.1",
      input_tokens: null,
      output_tokens: null,
      cached_tokens: null,
      cost_usd: null,
      pricing_version: null,
      usage_state: "usage_not_reported",
      attributes: { "usage.unreported_reason": "not_exported" },
    });
    for (const label of ["Input tokens", "Output tokens", "Cost"]) {
      expect(unknownLabel(label)).toBe("Unknown: The exporter sent no token counts");
    }
    expect(screen.getByText("gpt-4.1")).toBeInTheDocument();
    expect(screen.queryByText("Counted on child spans")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Not reported for this call/);
  });

  it("partial usage names the missing count", () => {
    renderCard({ output_tokens: null, cost_usd: null, usage_state: "partial_usage" });
    expect(screen.getByText("1,312")).toBeInTheDocument();
    expect(unknownLabel("Output tokens")).toBe("Unknown: The exporter sent only input tokens");
    expect(unknownLabel("Cost")).toBe("Unknown: Usage is incomplete, so no cost was computed");
  });

  it("partial usage names the output count when the input count is missing", () => {
    renderCard({ input_tokens: null, cost_usd: null, usage_state: "partial_usage" });
    expect(unknownLabel("Input tokens")).toBe("Unknown: The exporter sent only output tokens");
  });

  it("blames the missing model name for an unknown cost", () => {
    renderCard({ model: null, cost_usd: null, usage_state: "model_unknown" });
    expect(unknownLabel("Cost")).toBe("Unknown: No model name on this span");
  });

  it("priced span shows no badge", () => {
    renderCard({});
    expect(screen.queryByText("Counted on child spans")).not.toBeInTheDocument();
  });
});
