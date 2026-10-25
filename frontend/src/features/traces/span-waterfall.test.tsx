import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Insight, Span } from "@/lib/api";

import { buildSpanTree } from "./span-tree";
import { testSpan } from "./span-test-data";
import { SpanWaterfall } from "./span-waterfall";

const BASE = Date.parse("2026-10-07T12:00:00.000Z");

function span(spanId: string, parentSpanId: string | null, status: Span["status"] = "ok"): Span {
  const offset = Number(spanId.replace(/\D/g, "")) || 0;
  return {
    span_id: spanId,
    parent_span_id: parentSpanId,
    kind: "other",
    name: spanId,
    status,
    status_message: null,
    error_class: null,
    provider_error_type: null,
    provider_error_code: null,
    rate_limit_kind: null,
    started_at: new Date(BASE + offset).toISOString(),
    ended_at: new Date(BASE + offset + 10).toISOString(),
    duration_ms: 10,
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
    input: null,
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
}

function Harness({
  spans,
  initial = null,
  insights,
}: {
  spans: Span[];
  initial?: string | null;
  insights?: Insight[];
}) {
  const [selected, setSelected] = useState<string | null>(initial);
  return (
    <TooltipProvider>
      <SpanWaterfall
        spans={spans}
        roots={buildSpanTree(spans)}
        selectedSpanId={selected}
        onSelect={setSelected}
        insights={insights}
      />
    </TooltipProvider>
  );
}

/** A root with a healthy "plan" subtree of 100 spans, a failed path and a leaf. */
function bigTrace(): Span[] {
  return [
    span("root", null),
    span("plan", "root"),
    ...Array.from({ length: 100 }, (_, index) => span(`plan-step-${index + 1}`, "plan")),
    span("lookup", "root"),
    span("http", "lookup", "error"),
    span("chat", "root"),
  ];
}

function row(spanId: string): HTMLElement {
  const element = document.getElementById(`span-row-${spanId}`);
  expect(element).toHaveAttribute("role", "treeitem");
  return element as HTMLElement;
}

describe("SpanWaterfall on a trace over 100 spans", () => {
  it("starts healthy subtrees collapsed and keeps the failed path open", () => {
    render(<Harness spans={bigTrace()} />);

    expect(row("plan")).toHaveAttribute("aria-expanded", "false");
    expect(within(row("plan")).getByText("+100 spans")).toBeInTheDocument();
    expect(row("lookup")).toHaveAttribute("aria-expanded", "true");
    expect(row("http")).toBeInTheDocument();
    expect(document.getElementById("span-row-plan-step-1")).toBeNull();
  });

  it("opens the selected span's ancestors", () => {
    render(<Harness spans={bigTrace()} initial="plan-step-7" />);

    expect(row("plan")).toHaveAttribute("aria-expanded", "true");
    expect(row("plan-step-7")).toHaveAttribute("aria-selected", "true");
  });
});

describe("SpanWaterfall on 1,000 visible spans", () => {
  // The last child failed, so the root stays open and all 1,000 rows are visible.
  const spans = [
    span("s0", null),
    ...Array.from({ length: 999 }, (_, index) =>
      span(`s${index + 1}`, "s0", index === 998 ? "error" : "ok"),
    ),
  ];

  it("renders only a window of rows, each announcing its place in the tree", () => {
    render(<Harness spans={spans} />);

    const rows = screen.getAllByRole("treeitem");
    expect(rows.length).toBeLessThan(100);
    expect(rows[1]).toHaveAttribute("aria-level", "2");
    expect(rows[1]).toHaveAttribute("aria-setsize", "999");
    expect(rows[1]).toHaveAttribute("aria-posinset", "1");
  });

  it("scrolls a span selected from outside into view without moving focus", () => {
    const roots = buildSpanTree(spans);
    const scrollTo = vi.spyOn(window, "scrollTo");
    const { rerender } = render(
      <SpanWaterfall spans={spans} roots={roots} selectedSpanId="s0" onSelect={vi.fn()} />,
      { wrapper: TooltipProvider },
    );
    const before = document.activeElement;
    scrollTo.mockClear();

    rerender(
      <SpanWaterfall spans={spans} roots={roots} selectedSpanId="s900" onSelect={vi.fn()} />,
    );

    // jsdom's page has no height, so the virtualizer clamps the offset; the call is what matters.
    expect(scrollTo).toHaveBeenCalled();
    expect(document.activeElement).toBe(before);
  });

  it("End, Home and ArrowDown focus rows outside the rendered window", async () => {
    const user = userEvent.setup();
    render(<Harness spans={spans} />);
    screen.getAllByRole("treeitem")[0]?.focus();

    await user.keyboard("{End}");
    expect(document.activeElement).toHaveAttribute("id", "span-row-s999");
    expect(document.activeElement).toHaveAttribute("aria-posinset", "999");

    await user.keyboard("{Home}");
    expect(document.activeElement).toHaveAttribute("id", "span-row-s0");

    await user.keyboard("{End}{ArrowUp}");
    expect(document.activeElement).toHaveAttribute("id", "span-row-s998");

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toHaveAttribute("id", "span-row-s999");

    await user.keyboard("{Home}{ArrowDown}");
    expect(document.activeElement).toHaveAttribute("id", "span-row-s1");
  });
});

describe("SpanWaterfall with its own scroll box", () => {
  it("virtualizes against the given element instead of the window", () => {
    const spans = [
      span("s0", null),
      ...Array.from({ length: 999 }, (_, i) => span(`s${i + 1}`, "s0")),
    ];
    const box = document.createElement("div");
    const scrollTo = vi.fn();
    box.scrollTo = scrollTo;
    const windowScroll = vi.spyOn(window, "scrollTo");
    const roots = buildSpanTree(spans);
    const { rerender } = render(
      <SpanWaterfall
        spans={spans}
        roots={roots}
        selectedSpanId="s0"
        onSelect={vi.fn()}
        scrollElement={box}
      />,
      { wrapper: TooltipProvider },
    );
    expect(screen.getAllByRole("treeitem").length).toBeLessThan(100);
    windowScroll.mockClear();

    rerender(
      <SpanWaterfall
        spans={spans}
        roots={roots}
        selectedSpanId="s900"
        onSelect={vi.fn()}
        scrollElement={box}
      />,
    );

    expect(scrollTo).toHaveBeenCalled();
    expect(windowScroll).not.toHaveBeenCalled();
  });
});

describe("SpanWaterfall toolbar", () => {
  const traceSpans = [
    testSpan("root", null, 0, 100, { kind: "chain", name: "answer_ticket" }),
    testSpan("plan", "root", 1, 20, { kind: "chain", name: "plan" }),
    testSpan("lookup", "plan", 2, 10, { kind: "tool", name: "lookup_order" }),
    testSpan("chat", "root", 30, 50, { kind: "llm", name: "chat", output: "Order shipped" }),
  ];

  it("filters by kind, keeping the ancestors as context rows", async () => {
    const user = userEvent.setup();
    render(<Harness spans={traceSpans} />);

    await user.click(screen.getByRole("radio", { name: "Tool" }));

    expect(row("lookup")).toBeInTheDocument();
    expect(row("plan")).toBeInTheDocument();
    expect(row("root")).toBeInTheDocument();
    expect(document.getElementById("span-row-chat")).toBeNull();
    expect(screen.queryByRole("radio", { name: "Retrieval" })).toBeNull();
  });

  it("finds spans by name and loaded output, and Enter steps through the matches", async () => {
    const user = userEvent.setup();
    render(<Harness spans={traceSpans} />);

    await user.type(screen.getByRole("searchbox", { name: "Find span" }), "order{Enter}");

    expect(screen.getByRole("status")).toHaveTextContent("1 of 2");
    expect(row("lookup")).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Enter}");
    expect(screen.getByRole("status")).toHaveTextContent("2 of 2");
    expect(row("chat")).toHaveAttribute("aria-selected", "true");
  });

  it("collapses retries into one row and shows the attempts when switched off", async () => {
    const user = userEvent.setup();
    const attempt = (id: string, start: number, status: Span["status"]) =>
      testSpan(id, "root", start, 10, { kind: "llm", name: "chat", request_hash: "h", status });
    render(
      <Harness
        spans={[
          testSpan("root", null, 0, 100),
          attempt("a1", 1, "error"),
          attempt("a2", 20, "error"),
          attempt("a3", 40, "ok"),
        ]}
      />,
    );

    expect(within(row("a3")).getByText("3 attempts · 2 failed")).toBeInTheDocument();
    // On screen the badge is short, so a narrow name column still shows the span's name.
    expect(within(row("a3")).getByText("3× · 2 failed")).toHaveAttribute("aria-hidden", "true");
    // The failed count sits in the toolbar heading, not in a badge that would wrap the row.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("4 spans · 2 failed");
    expect(document.getElementById("span-row-a1")).toBeNull();

    await user.click(screen.getByRole("switch", { name: "Collapse retries" }));

    expect(row("a1")).toBeInTheDocument();
    expect(row("a2")).toBeInTheDocument();
    expect(screen.queryByText("3 attempts · 2 failed")).toBeNull();
  });

  it("puts the count, find, kind filter and expand/collapse in one row above the time axis", () => {
    render(<Harness spans={traceSpans} />);

    const toolbar = screen.getByRole("heading", { level: 2, name: "4 spans" }).parentElement;
    expect(toolbar).toHaveAttribute("data-slot", "tree-toolbar");
    const box = toolbar as HTMLElement;
    for (const control of [
      screen.getByRole("searchbox", { name: "Find span" }).parentElement,
      screen.getByRole("radiogroup", { name: "Span kind" }),
      screen.getByRole("combobox", { name: "Kind" }),
      screen.getByRole("button", { name: "Expand all" }).parentElement,
    ]) {
      expect(control?.parentElement).toBe(box);
    }
    // Nothing else sits between the toolbar and the rows: the card holds toolbar, rows, legend.
    expect(box.nextElementSibling?.querySelector("[role=tree]")).not.toBeNull();
  });

  it("draws an inline bar under the name for a narrow pane, where the timeline cell hides", () => {
    render(<Harness spans={traceSpans} />);

    const cells = [...row("chat").children];
    expect(cells[1]?.className).toContain("hidden @min-[420px]:block");
    const inline = cells[0]?.querySelector('[class*="@min-[420px]:hidden"]');
    expect(inline?.querySelector(".bg-kind-llm")).not.toBeNull();
  });

  it("hides the retries switch when the trace has no retry group", () => {
    render(<Harness spans={traceSpans} />);
    expect(screen.queryByRole("switch", { name: "Collapse retries" })).toBeNull();
  });
});

describe("SpanWaterfall rows", () => {
  it("flags the span the Doctor reads", () => {
    const insight = { id: "ins_1", title: "Truncated outputs", severity: "warning" } as Insight;
    render(
      <Harness
        spans={[
          testSpan("root", null, 0, 100, { kind: "chain" }),
          testSpan("chat", "root", 10, 10, { kind: "llm" }),
        ]}
        insights={[insight]}
      />,
    );

    expect(
      within(row("chat")).getByRole("img", { name: "Doctor: Truncated outputs" }),
    ).toBeInTheDocument();
  });

  it("flags the retry group when the span the Doctor reads is a folded attempt", () => {
    const insight = { id: "ins_1", title: "Retry storm", severity: "critical" } as Insight;
    const attempt = (id: string, start: number, status: Span["status"]) =>
      testSpan(id, "root", start, 10, { kind: "llm", name: "chat", request_hash: "h", status });
    render(
      <Harness
        spans={[
          testSpan("root", null, 0, 100, { kind: "chain" }),
          attempt("a1", 1, "error"),
          attempt("a2", 20, "ok"),
          attempt("a3", 40, "ok"),
        ]}
        insights={[insight]}
      />,
    );

    expect(document.getElementById("span-row-a1")).toBeNull();
    expect(within(row("a3")).getByRole("img", { name: "Doctor: Retry storm" })).toBeInTheDocument();
  });

  it("announces a parent's self time", () => {
    render(<Harness spans={[testSpan("root", null, 0, 100), testSpan("child", "root", 10, 70)]} />);
    expect(row("root")).toHaveTextContent("self 30 ms");
  });

  it("shows a ghost row for a missing parent, which ArrowDown skips", async () => {
    const user = userEvent.setup();
    render(
      <Harness spans={[testSpan("root", null, 0, 100), testSpan("orphan", "7f3a9c01", 10, 10)]} />,
    );

    const ghost = screen.getByText("Missing parent span · 7f3a…").closest("[role=treeitem]");
    expect(ghost).toHaveAttribute("aria-disabled", "true");
    // The ghost counts among the top-level rows: root 1, ghost 2, orphan 3 of 3.
    expect(row("root")).toHaveAttribute("aria-posinset", "1");
    expect(ghost).toHaveAttribute("aria-posinset", "2");
    expect(row("orphan")).toHaveAttribute("aria-posinset", "3");
    expect(row("orphan")).toHaveAttribute("aria-setsize", "3");
    row("root").focus();
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(row("orphan"));
  });
});
