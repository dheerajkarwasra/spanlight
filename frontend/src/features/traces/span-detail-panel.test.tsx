import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Span } from "@/lib/api";

import { SpanDetailPanel } from "./span-detail-panel";

vi.mock("./span-replays-tab", () => ({ SpanReplaysTab: () => null }));
vi.mock("./payload-view", () => ({
  PayloadView: ({ label }: { label: string }) => <p>{label}</p>,
}));
vi.mock("./span-cost-card", () => ({ SpanCostCard: () => null }));
vi.mock("@/features/scores", () => ({
  ScoresPanel: () => <p>Trace scores</p>,
  SpanScores: () => <p>Span scores</p>,
}));

const span: Span = {
  span_id: "span_1",
  parent_span_id: null,
  kind: "chain",
  name: "answer_ticket",
  status: "ok",
  status_message: null,
  error_class: null,
  provider_error_type: null,
  provider_error_code: null,
  rate_limit_kind: null,
  started_at: "2026-10-21T10:00:00Z",
  ended_at: "2026-10-21T10:00:02Z",
  duration_ms: 2000,
  provider: null,
  model: null,
  input_tokens: null,
  output_tokens: null,
  cached_tokens: null,
  cache_write_tokens: null,
  cache_write_1h_tokens: null,
  service_tier: null,
  web_search_requests: null,
  cost_usd: null,
  fee_usd: null,
  pricing_version: null,
  time_to_first_token_ms: null,
  input: "Line one.\nLine two.",
  output: null,
  attributes: {},
  truncated: false,
  payload_omitted: false,
  finish_reason: null,
  prompt_version_id: null,
  usage_rollup: false,
  usage_state: "not_applicable",
  request_hash: null,
};

function renderPanel(target: Span, tab: string | undefined, onTabChange = vi.fn()) {
  render(
    <TooltipProvider>
      <SpanDetailPanel
        span={target}
        tab={tab}
        onTabChange={onTabChange}
        inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
      />
    </TooltipProvider>,
  );
}

function tabNames() {
  return screen.getAllByRole("tab").map((tab) => tab.textContent);
}

const llmSpan: Span = { ...span, kind: "llm", model: "gpt-4.1", input_tokens: 10 };

describe("SpanDetailPanel tabs", () => {
  it("offers Cost and Replays only on an LLM span, in the inspector's order", () => {
    renderPanel(span, undefined);
    expect(tabNames()).toEqual(["Messages", "Scores", "Attributes", "Raw JSON"]);
    expect(screen.getByRole("tab", { name: "Messages" })).toHaveAttribute("aria-selected", "true");
  });

  it("lists every tab on an LLM span", () => {
    renderPanel(llmSpan, "cost");
    expect(tabNames()).toEqual(["Messages", "Cost", "Scores", "Attributes", "Raw JSON", "Replays"]);
    expect(screen.getByRole("tab", { name: "Cost" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Usage" })).toBeInTheDocument();
  });

  it("opens Attributes for an old metadata link", () => {
    renderPanel(span, "metadata");
    expect(screen.getByRole("tab", { name: "Attributes" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows the trace's scores, then the span's, on the Scores tab", () => {
    renderPanel(span, "scores");
    const panel = screen.getByRole("tabpanel");
    expect(
      within(panel)
        .getAllByText(/scores$/)
        .map((node) => node.textContent),
    ).toEqual(["Trace scores", "Span scores"]);
  });

  it("reports the chosen tab by its URL value", async () => {
    const onTabChange = vi.fn();
    renderPanel(span, undefined, onTabChange);
    await userEvent.click(screen.getByRole("tab", { name: "Raw JSON" }));
    expect(onTabChange).toHaveBeenCalledWith("raw");
  });

  it("has no Replay button in the span header (the summary bar has the one)", () => {
    renderPanel(llmSpan, undefined);
    expect(screen.queryByRole("link", { name: /Replay/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Replay/ })).not.toBeInTheDocument();
  });
});

describe("SpanDetailPanel — Attributes tab", () => {
  it("folds the transport attributes under their own disclosure", () => {
    renderPanel(
      {
        ...span,
        attributes: {
          "app.feature": "triage",
          "http.method": "POST",
          "server.address": "api.openai.com",
        },
      },
      "attributes",
    );
    const panel = screen.getByRole("tabpanel");
    const summary = within(panel).getByText("Transport attributes (2)");
    const details = summary.closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(within(details as HTMLElement).getByText(/"POST"|POST/)).toBeInTheDocument();
    expect(within(panel).getByText("3")).toBeInTheDocument();
  });
});

describe("SpanDetailPanel — Raw JSON tab", () => {
  it("stays raw: multi-line strings are JSON literals and there is no prose toggle", () => {
    render(
      <TooltipProvider>
        <SpanDetailPanel
          span={span}
          tab="raw"
          onTabChange={vi.fn()}
          inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
        />
      </TooltipProvider>,
    );

    expect(screen.getByText(JSON.stringify(span.input))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View as JSON" })).not.toBeInTheDocument();
  });
});
