import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { projectsApi, type CompareStep, type Span, type TraceComparison } from "@/lib/api";

import { CompareSteps } from "./compare-steps";
import { traceCompareSearchSchema } from "./search";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const side = {
  kind: "llm" as const,
  status: "ok" as const,
  model: "gpt-4.1",
  duration_ms: 200,
  input_tokens: 10,
  output_tokens: 5,
  cost_usd: "0.001",
};

const STEP: CompareStep = {
  name: "chat",
  occurrence: 1,
  a: { ...side, span_id: "span_a" },
  b: { ...side, span_id: "span_b" },
  duration_ms: 0,
  input_tokens: 0,
  output_tokens: 0,
  cost_usd: "0",
  input_changed: false,
  output_changed: true,
};

const COMPARISON = {
  a: { trace_id: "trace_a" },
  b: { trace_id: "trace_b" },
  steps: [STEP],
} as unknown as TraceComparison;

const PAYLOADS: Record<string, Partial<Span>> = {
  span_a: { input: "Where is my order?", output: "Shipped\nArrives Monday" },
  span_b: { input: "Where is my order?", output: "Shipped\nArrives Tuesday" },
};

function renderSteps() {
  const rootRoute = createRootRoute();
  const authed = createRoute({ getParentRoute: () => rootRoute, id: "_authed" });
  const compare = createRoute({
    getParentRoute: () => authed,
    path: "$orgId/$projectId/traces/compare",
    validateSearch: traceCompareSearchSchema,
    component: () => <CompareSteps comparison={COMPARISON} />,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([authed.addChildren([compare])]),
    history: createMemoryHistory({
      initialEntries: ["/org_1/proj_1/traces/compare?a=trace_a&b=trace_b"],
    }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CompareSteps' Show diff", () => {
  it("loads both sides only when opened and marks removed and added lines", async () => {
    const span = vi
      .spyOn(projectsApi, "span")
      .mockImplementation((_project, _trace, spanId) =>
        Promise.resolve({ span_id: spanId, ...PAYLOADS[spanId] } as Span),
      );
    const user = userEvent.setup();
    renderSteps();

    const toggle = await screen.findByRole("button", { name: "Show diff of chat" });
    expect(span).not.toHaveBeenCalled();
    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(span).toHaveBeenCalledWith("proj_1", "trace_a", "span_a");
    expect(span).toHaveBeenCalledWith("proj_1", "trace_b", "span_b");
    const outputA = await screen.findByRole("list", { name: "A · Baseline output" });
    const outputB = screen.getByRole("list", { name: "B · Candidate output" });
    expect(within(outputA).getByText("Removed:").parentElement).toHaveTextContent(
      "Removed: Arrives Monday",
    );
    expect(within(outputA).queryByText(/Tuesday/)).not.toBeInTheDocument();
    expect(within(outputB).getByText("Added:").parentElement).toHaveTextContent(
      "Added: Arrives Tuesday",
    );
    // The unchanged line shows on both sides, unmarked.
    expect(within(outputA).getByText("Shipped")).toBeInTheDocument();
    expect(within(outputB).getByText("Shipped")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Hide diff of chat" }));
    expect(screen.queryByRole("list", { name: "A · Baseline output" })).not.toBeInTheDocument();
  });
});

describe("CompareSteps' Show diff of a big payload", () => {
  it("diffs only the first 16 KB of a side over 64 KB and says so", async () => {
    const big = "x".repeat(200 * 1024);
    vi.spyOn(projectsApi, "span").mockImplementation((_project, _trace, spanId) =>
      Promise.resolve({ span_id: spanId, input: null, output: `${big}${spanId}` } as Span),
    );
    const user = userEvent.setup();
    renderSteps();

    await user.click(await screen.findByRole("button", { name: "Show diff of chat" }));
    expect(
      await screen.findByText("Showing the first 16 KB of each side; open the step for all."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in A" })).toHaveAttribute(
      "href",
      "/org_1/proj_1/traces/trace_a?span=span_a",
    );
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let longest = 0;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      longest = Math.max(longest, node.textContent?.length ?? 0);
    }
    expect(longest).toBeGreaterThan(0);
    expect(longest).toBeLessThanOrEqual(16 * 1024);
  });
});

describe("CompareSteps' Show diff of one big side", () => {
  it("shows no change from the cut when only A passes the guard", async () => {
    // 1,005-byte lines: the 16 KB cut lands mid-line.
    const line = (index: number) => `${String(index).padStart(4, "0")}${"z".repeat(1000)}`;
    const lines = (count: number) =>
      Array.from({ length: count }, (_, index) => line(index)).join("\n");
    const outputs: Record<string, string> = { span_a: lines(70), span_b: lines(20) };
    vi.spyOn(projectsApi, "span").mockImplementation((_project, _trace, spanId) =>
      Promise.resolve({ span_id: spanId, input: null, output: outputs[spanId] } as Span),
    );
    const user = userEvent.setup();
    renderSteps();

    await user.click(await screen.findByRole("button", { name: "Show diff of chat" }));
    expect(
      await screen.findByText("Showing the first 16 KB of each side; open the step for all."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Added:")).not.toBeInTheDocument();
    expect(screen.queryByText("Removed:")).not.toBeInTheDocument();
  });
});
