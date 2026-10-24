import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { costExplanationApi, insightsApi, type Span, type TraceDetail } from "@/lib/api";

import { traceDetailSearchSchema } from "./search";
import { TraceView, type PeekSelection } from "./trace-view";

const shell = vi.hoisted(() => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
  usePermission: () => true,
}));
vi.mock("@/features/shell/project-context", () => shell);
vi.mock("@/features/shell", () => shell);
vi.mock("./trace-actions", () => ({ TraceActions: () => null }));
vi.mock("./insight-badges", () => ({
  InsightBadges: ({ compact }: { compact?: boolean }) => (
    <p>Doctor findings{compact ? " (compact)" : ""}</p>
  ),
}));
vi.mock("./span-replays-tab", () => ({ SpanReplaysTab: () => null }));
vi.mock("@/features/scores", () => ({ ScoresPanel: () => null, SpanScores: () => null }));
vi.mock("./replay-button", () => ({
  ReplayButton: ({ span }: { span: Span }) => <p>Replay target: {span.span_id}</p>,
}));

function makeSpan(id: string, overrides: Partial<Span>): Span {
  return {
    span_id: id,
    parent_span_id: null,
    kind: "chain",
    name: id,
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
    input: "Where is my order?",
    output: "On its way.",
    attributes: {},
    truncated: false,
    payload_omitted: false,
    finish_reason: null,
    prompt_version_id: null,
    usage_rollup: false,
    usage_state: "not_applicable",
    request_hash: null,
    ...overrides,
  };
}

const LLM_USAGE: Partial<Span> = {
  kind: "llm",
  provider: "openai",
  model: "gpt-4.1",
  usage_state: "priced",
};

const SPANS: Span[] = [
  makeSpan("root", { name: "answer_ticket" }),
  makeSpan("call_1", {
    ...LLM_USAGE,
    parent_span_id: "root",
    input_tokens: 4000,
    output_tokens: 500,
    // Input tokens include the cached ones (the SDK and server convention).
    cached_tokens: 3000,
    cost_usd: "0.01",
  }),
  makeSpan("call_2", {
    ...LLM_USAGE,
    parent_span_id: "root",
    input_tokens: 1104,
    output_tokens: 227,
    cached_tokens: 400,
    cost_usd: "0.0042",
  }),
  // A rollup repeats its call's usage: its cached tokens are not counted again.
  makeSpan("rollup", {
    parent_span_id: "root",
    input_tokens: 4000,
    cached_tokens: 3000,
    usage_rollup: true,
  }),
];

const TRACE: TraceDetail = {
  trace_id: "trace_1",
  name: "answer_ticket",
  environment: "production",
  release: null,
  external_user_id: null,
  session_id: null,
  tags: [],
  started_at: "2026-10-21T10:00:00Z",
  ended_at: "2026-10-21T10:00:02Z",
  duration_ms: 2000,
  span_count: SPANS.length,
  error_count: 0,
  input_tokens: 5104,
  output_tokens: 727,
  cost_usd: "0.0142",
  has_unpriced: false,
  models: ["gpt-4.1"],
  error_message: null,
  error_class: null,
  has_scores: false,
  retry_calls: 0,
  retry_cost_usd: "0",
  scores: [],
  metadata: {},
  spans: SPANS,
};

/** The peek's selection lives in the sheet; this stands in for it. */
function PeekHarness({ spans }: { spans: Span[] }) {
  const [selection, setSelection] = useState<PeekSelection>({});
  return (
    <TraceView
      trace={TRACE}
      spans={spans}
      variant="peek"
      selection={selection}
      onSelectionChange={setSelection}
      scrollElement={null}
      linkHref="/org_1/proj_1/traces/trace_1"
    />
  );
}

function renderView(variant: "page" | "peek", search = "", spans = TRACE.spans) {
  const rootRoute = createRootRoute();
  const authed = createRoute({ getParentRoute: () => rootRoute, id: "_authed" });
  const traceRoute = createRoute({
    getParentRoute: () => authed,
    path: "$orgId/$projectId/traces/$traceId",
    validateSearch: traceDetailSearchSchema,
    component: () =>
      variant === "page" ? (
        <TraceView trace={{ ...TRACE, spans }} spans={spans} variant="page" />
      ) : (
        <PeekHarness spans={spans} />
      ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([authed.addChildren([traceRoute])]),
    history: createMemoryHistory({ initialEntries: [`/org_1/proj_1/traces/trace_1${search}`] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        {/* The test tree is not the registered app router; the view only needs its route id. */}
        <RouterProvider router={router as never} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return router;
}

function treeItem(name: string) {
  return screen.getByRole("treeitem", { name: new RegExp(name) });
}

beforeEach(() => {
  window.localStorage.clear();
  // The waterfall's Doctor flag reads the trace's insights; none arrive here.
  vi.spyOn(insightsApi, "list").mockReturnValue(new Promise(() => undefined));
});

describe("TraceView summary bar", () => {
  it("shows the name, duration, tokens with the cached share, and the cost", async () => {
    renderView("page");

    const name = await screen.findByRole("heading", { level: 1, name: "answer_ticket" });
    const bar = name.closest("div.sticky") as HTMLElement;
    expect(within(bar).getByText("Duration").nextElementSibling).toHaveTextContent("2.00 s");
    expect(within(bar).getByText("Tokens").nextElementSibling).toHaveTextContent(
      /^5,104 → in, 727 out · , 3,400 cached$/,
    );
    expect(within(bar).getByText("Cost").nextElementSibling).toHaveTextContent("$0.0142");
    // The narrow bar's second line (Figma 375): duration · tokens · cost, no cached share.
    expect(name.nextElementSibling).toHaveTextContent(
      /^2\.00 s · 5,104 → in, 727 out · \$0\.0142$/,
    );
    // Doctor findings sit in the bar at every width (wrapping below @3xl), not in Trace info.
    expect(within(bar).getByText("Doctor findings (compact)")).toBeInTheDocument();
  });

  it("shows unknown tokens, not 0 → 0, when no call reported usage", async () => {
    const noUsage = TRACE.spans.map((span) => ({
      ...span,
      input_tokens: null,
      output_tokens: null,
    }));
    renderView("page", "", noUsage);

    const name = await screen.findByRole("heading", { level: 1, name: "answer_ticket" });
    const bar = name.closest("div.sticky") as HTMLElement;
    expect(within(bar).getByText("Tokens").nextElementSibling).toHaveTextContent(/^—$/);
    expect(
      within(bar).getAllByLabelText("Unknown: No call in this trace reported token usage"),
    ).not.toHaveLength(0);
  });

  it("keeps Trace info closed until its button opens it", async () => {
    const user = userEvent.setup();
    renderView("page");

    const button = await screen.findByRole("button", { name: "Trace info" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "Trace info" })).not.toBeInTheDocument();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("region", { name: "Trace info" });
    expect(button).toHaveAttribute("aria-controls", panel.id);
    expect(within(panel).getByText("trace_1")).toBeInTheDocument();
    expect(within(panel).getByText("production")).toBeInTheDocument();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "Trace info" })).not.toBeInTheDocument();
  });

  it("copies the address with its span and tab, and says so", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const success = vi.spyOn(toast, "success");
    renderView("page", "?span=call_1&tab=cost");

    await user.click(await screen.findByRole("button", { name: "Copy link" }));

    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/org_1/proj_1/traces/trace_1?span=call_1&tab=cost`,
    );
    expect(success).toHaveBeenCalledWith("Link copied");
  });

  it("replays the selected LLM span, else the trace's first LLM span", async () => {
    const user = userEvent.setup();
    renderView("page", "?span=root");

    expect(await screen.findByText("Replay target: call_1")).toBeInTheDocument();
    await user.click(treeItem("call_2"));
    expect(await screen.findByText("Replay target: call_2")).toBeInTheDocument();
    // One Replay: the inspector's span header no longer has its own.
    expect(screen.getAllByText(/Replay target/)).toHaveLength(1);
  });
});

describe("TraceView inspector tabs", () => {
  it("shows the span's usage and its expanded cost breakdown on the Cost tab", async () => {
    vi.spyOn(costExplanationApi, "get").mockReturnValue(new Promise(() => undefined));
    renderView("page", "?span=call_1&tab=cost");

    expect(await screen.findByRole("tab", { name: "Cost", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Usage" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cost breakdown" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide cost breakdown" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(costExplanationApi.get).toHaveBeenCalledWith("proj_1", "trace_1", "call_1");
  });

  it("falls back to Messages for a tab the span doesn't have, keeping the URL", async () => {
    const router = renderView("page", "?span=root&tab=cost");

    expect(await screen.findByRole("tab", { name: "Messages", selected: true })).toBeVisible();
    expect(screen.queryByRole("tab", { name: "Cost" })).not.toBeInTheDocument();
    expect(router.state.location.search).toMatchObject({ span: "root", tab: "cost" });
  });

  it("writes Messages to the URL when it is chosen while showing as the fallback", async () => {
    const user = userEvent.setup();
    const router = renderView("page", "?span=root&tab=cost");

    await user.click(await screen.findByRole("tab", { name: "Messages", selected: true }));

    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ span: "root", tab: "messages" });
    });
  });

  it("writes the chosen tab to the URL on the page", async () => {
    const user = userEvent.setup();
    const router = renderView("page");

    await user.click(await screen.findByRole("tab", { name: "Attributes" }));

    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ tab: "attributes" });
    });
    expect(screen.getByRole("tab", { name: "Attributes", selected: true })).toBeInTheDocument();
  });

  it("keeps the tab and span in the view, not the URL, in the peek", async () => {
    const user = userEvent.setup();
    const router = renderView("peek");

    await user.click(await screen.findByRole("tab", { name: "Raw JSON" }));
    await user.click(treeItem("call_2"));

    expect(screen.getByRole("tab", { name: "Raw JSON", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "call_2" })).toBeInTheDocument();
    expect(router.state.location.search).toEqual({});
  });
});

describe("TraceView stacked panes", () => {
  /** scrollIntoView calls made on the element holding the inspector. */
  function inspectorScrolls(spy: { mock: { contexts: unknown[] } }) {
    const inspector = screen.getByRole("region", { name: /^Span / });
    return spy.mock.contexts.filter(
      (element) => element instanceof Element && element.contains(inspector),
    ).length;
  }

  it("brings the inspector into view for a clicked row, not for arrow-key moves", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderView("page", "?span=root");

    await user.click(await screen.findByRole("treeitem", { name: /call_1/ }));
    expect(inspectorScrolls(scroll)).toBe(1);

    scroll.mockClear();
    await user.keyboard("{ArrowDown}");
    expect(await screen.findByRole("heading", { level: 2, name: "call_2" })).toBeInTheDocument();
    expect(inspectorScrolls(scroll)).toBe(0);
  });

  it("leaves the inspector where it is when a missing-parent row is clicked", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderView("page", "?span=root", [...SPANS, makeSpan("orphan", { parent_span_id: "gone" })]);

    await user.click(await screen.findByText(/Missing parent span/));
    expect(inspectorScrolls(scroll)).toBe(0);
  });
});

describe("TraceView tree pane", () => {
  it("virtualizes a big tree against its rows box, which scrolls on its own under the toolbar", async () => {
    // The last call failed, so the root stays open and all 400 rows are visible.
    const spans = [
      makeSpan("root", {}),
      ...Array.from({ length: 399 }, (_, index) =>
        makeSpan(`s${index + 1}`, {
          parent_span_id: "root",
          status: index === 398 ? "error" : "ok",
        }),
      ),
    ];
    // jsdom lays nothing out: give every box the pane's 220 px so the virtualizer has a viewport.
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(220);
    renderView("peek", "", spans);

    await screen.findByRole("heading", { level: 1 });
    const pane = screen.getByRole("tree").closest(".overflow-auto") as HTMLElement;
    // The 220 px cap holds the rows only: the toolbar stays above the scroll box.
    expect(pane).toHaveClass("max-h-[220px]");
    expect(within(pane).queryByRole("searchbox", { name: "Find span" })).toBeNull();
    expect(document.getElementById("span-row-s1")).toBeInTheDocument();
    Object.defineProperty(pane, "scrollTop", { configurable: true, value: 300 * 42 });
    pane.dispatchEvent(new Event("scroll"));

    // Scrolling the pane (not the page) brings the deep rows in and lets the top ones go.
    await waitFor(() => {
      expect(document.getElementById("span-row-s1")).toBeNull();
    });
    expect(document.querySelectorAll("[role=treeitem]").length).toBeLessThan(40);
  });
});
