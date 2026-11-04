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
  attributes_omitted: false,
  retry_eligible: false,
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

describe("SpanDetailPanel — span content being loaded", () => {
  const incomplete: Span = { ...span, attributes: {}, attributes_omitted: true };

  it("keeps the header and tabs, shows the loading note and disables Copy JSON", () => {
    render(
      <TooltipProvider>
        <SpanDetailPanel
          span={incomplete}
          tab="raw"
          onTabChange={vi.fn()}
          inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
          content={{ status: "pending" }}
        />
      </TooltipProvider>,
    );

    expect(screen.getByRole("heading", { name: span.name })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Raw JSON" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Loading span content…");
    expect(screen.getByRole("button", { name: "Copy JSON" })).toBeDisabled();
    expect(screen.getByText("Available after span content loads.")).toBeInTheDocument();
    expect(screen.queryByText('"attributes_omitted"')).toBeNull();
    expect(screen.getByRole("button", { name: "Copy span ID" })).toBeEnabled();
  });

  it("offers Retry on the Attributes tab when the content could not be loaded", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <TooltipProvider>
        <SpanDetailPanel
          span={incomplete}
          tab="attributes"
          onTabChange={vi.fn()}
          inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
          content={{ status: "error", error: new Error("boom"), onRetry }}
        />
      </TooltipProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Couldn’t load span content");
    expect(alert).toHaveTextContent("Try again to view stored attributes and copy complete JSON.");
    expect(screen.queryByText("No custom attributes on this span.")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("moves focus to the status line after Retry, so the outcome is announced", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const panel = (content: NonNullable<Parameters<typeof SpanDetailPanel>[0]["content"]>) => (
      <TooltipProvider>
        <SpanDetailPanel
          span={incomplete}
          tab="attributes"
          onTabChange={vi.fn()}
          inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
          content={content}
        />
      </TooltipProvider>
    );
    const { rerender } = render(panel({ status: "error", error: new Error("boom"), onRetry }));

    await user.click(screen.getByRole("button", { name: "Retry" }));
    rerender(panel({ status: "pending" }));

    expect(screen.getByRole("status")).toHaveTextContent("Loading span content…");
    expect(screen.getByRole("status")).toHaveFocus();
  });

  it("keeps focus in the tab panel when the retried content arrives", async () => {
    const user = userEvent.setup();
    const panel = (content: Parameters<typeof SpanDetailPanel>[0]["content"]) => (
      <TooltipProvider>
        <SpanDetailPanel
          span={content ? incomplete : span}
          tab="attributes"
          onTabChange={vi.fn()}
          inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
          content={content}
        />
      </TooltipProvider>
    );
    const { rerender } = render(
      panel({ status: "error", error: new Error("boom"), onRetry: vi.fn() }),
    );

    await user.click(screen.getByRole("button", { name: "Retry" }));
    rerender(panel({ status: "pending" }));
    rerender(panel(undefined));

    expect(screen.getByRole("tabpanel")).toHaveFocus();
  });

  it("moves focus to the failure message when the retry fails again", async () => {
    const user = userEvent.setup();
    const failed = { status: "error" as const, error: new Error("boom"), onRetry: vi.fn() };
    const panel = (content: NonNullable<Parameters<typeof SpanDetailPanel>[0]["content"]>) => (
      <TooltipProvider>
        <SpanDetailPanel
          span={incomplete}
          tab="attributes"
          onTabChange={vi.fn()}
          inTrace={{ traceId: "trace_1", childNodes: [], onSelectSpan: vi.fn() }}
          content={content}
        />
      </TooltipProvider>
    );
    const { rerender } = render(panel(failed));

    await user.click(screen.getByRole("button", { name: "Retry" }));
    rerender(panel({ status: "pending" }));
    rerender(panel(failed));

    const heading = screen.getByRole("heading", { name: "Couldn’t load span content" });
    expect(heading.parentElement).toHaveFocus();
  });

  it("enables Copy JSON once the content is complete, where the disabled one sat", () => {
    renderPanel(span, "raw");
    const copy = screen.getByRole("button", { name: "Copy JSON" });
    expect(copy).toBeEnabled();
    // Frames 822:2978 / 822:3319: text only and start-aligned, so loading does not move it.
    expect(copy.querySelector("svg")).toBeNull();
    expect(copy.parentElement).toHaveClass("items-start");
  });
});

describe("SpanDetailPanel — narrow tab strip", () => {
  it("scrolls the selected tab into view", () => {
    const rects = new Map<string, Partial<DOMRect>>([
      ["tablist", { left: 0, right: 300 }],
      ["Raw JSON", { left: 380, right: 460 }],
    ]);
    const spy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(function (this: HTMLElement) {
        const key = this.getAttribute("role") === "tablist" ? "tablist" : this.textContent;
        return { left: 0, right: 0, ...rects.get(key) } as DOMRect;
      });
    renderPanel(llmSpan, "raw");
    spy.mockRestore();

    // 460 − 300, plus the strip's 1 px border and 4 px padding so the pill is wholly visible.
    expect(screen.getByRole("tablist").scrollLeft).toBe(165);
  });
});
