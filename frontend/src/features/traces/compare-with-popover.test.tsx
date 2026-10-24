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
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { projectsApi, type TraceComparison, type TraceSummary } from "@/lib/api";

import { CompareWithPopover } from "./compare-with-popover";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const THIS = { trace_id: "this", name: "answer_ticket", started_at: "2026-10-21T10:00:00Z" };

function run(id: string, startedAt: string): TraceSummary {
  return { trace_id: id, started_at: startedAt, duration_ms: 1200 } as TraceSummary;
}

function renderPopover() {
  const rootRoute = createRootRoute();
  const trace = createRoute({
    getParentRoute: () => rootRoute,
    path: "$orgId/$projectId/traces/$traceId",
    component: () => <CompareWithPopover trace={THIS} />,
  });
  const compare = createRoute({
    getParentRoute: () => rootRoute,
    path: "$orgId/$projectId/traces/compare",
    component: () => <p>Compare page</p>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([compare, trace]),
    history: createMemoryHistory({ initialEntries: ["/org_1/proj_1/traces/this?env=prod"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router as never} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return router;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CompareWithPopover", () => {
  it("lists the previous run and recent runs of this name, without this trace", async () => {
    const list = vi.spyOn(projectsApi, "traces").mockResolvedValue({
      items: [
        run("this", THIS.started_at),
        run("prev_run_1", "2026-10-21T09:00:00Z"),
        run("prev_run_2", "2026-10-21T08:00:00Z"),
        run("prev_run_3", "2026-10-20T08:00:00Z"),
      ],
      next_cursor: null,
    });
    const user = userEvent.setup();
    const router = renderPopover();

    await user.click(await screen.findByRole("button", { name: "Compare with…" }));
    const previous = await screen.findByRole("region", { name: "Previous run of answer_ticket" });
    expect(list).toHaveBeenCalledWith("proj_1", {
      from: "2026-09-21T10:00:00.000Z",
      to: THIS.started_at,
      name: "answer_ticket",
      limit: 6,
    });
    expect(within(previous).getAllByRole("link")).toHaveLength(1);
    const recent = screen.getByRole("region", { name: "Recent runs of answer_ticket" });
    expect(within(recent).getAllByRole("link")).toHaveLength(2);
    expect(screen.queryByText("this")).not.toBeInTheDocument();

    await user.click(within(previous).getByRole("link"));
    await screen.findByText("Compare page");
    expect(router.state.location.search).toEqual({ env: "prod", a: "prev_run_1", b: "this" });
  });

  it("opens a pasted trace id with the earlier trace as A", async () => {
    vi.spyOn(projectsApi, "traces").mockResolvedValue({ items: [], next_cursor: null });
    // The pasted trace started after this one, so it becomes B.
    vi.spyOn(projectsApi, "compareTraces").mockResolvedValue({
      a: { started_at: "2026-10-21T11:00:00Z" },
      b: { started_at: THIS.started_at },
    } as TraceComparison);
    const user = userEvent.setup();
    const router = renderPopover();

    await user.click(await screen.findByRole("button", { name: "Compare with…" }));
    expect(await screen.findByText(/No earlier run/)).toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: "Paste a trace ID" });
    await user.type(input, "this{Enter}");
    expect(screen.getByText(/That's this trace/)).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, "  later_trace  {Enter}");
    await screen.findByText("Compare page");
    await waitFor(() => {
      expect(router.state.location.search).toEqual({ env: "prod", a: "this", b: "later_trace" });
    });
  });
});
