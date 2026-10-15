import { render, screen } from "@testing-library/react";
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
  cost_usd: "0.0164",
  pricing_version: "2026-10-01",
  time_to_first_token_ms: 412,
  input: null,
  output: null,
  attributes: {},
  truncated: false,
  finish_reason: null,
  prompt_version_id: null,
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
