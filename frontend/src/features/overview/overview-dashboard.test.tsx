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
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import {
  projectsApi,
  type AlertRule,
  type Budget,
  type Completeness,
  type Health,
  type Kpis,
} from "@/lib/api";

import { OverviewDashboard } from "./overview-dashboard";
import { OverviewHero } from "./overview-hero";
import { TracesTabsCard } from "./traces-tabs-card";
import type { OverviewQueries } from "./use-overview-queries";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "o", projectId: "p" }),
  useProjectFilters: () => ({
    range: {
      value: "24h",
      from: "2026-10-20T12:00:00.000Z",
      to: "2026-10-21T12:00:00.000Z",
      durationMs: 24 * 3600_000,
    },
    environment: undefined,
  }),
}));

const NOW = new Date("2026-10-21T12:00:00Z");

const KPIS: Kpis = {
  traces: 30,
  llm_calls: 432,
  error_rate: 15 / 432,
  p50_ms: 400,
  p95_ms: 1200,
  cost_usd: "1.50",
  unpriced_calls: 0,
  input_tokens: 100,
  output_tokens: 50,
};

const HEALTH = {
  value: 72,
  components: [],
  open_critical: 0,
  open_warning: 0,
  approximate: false,
} as unknown as Health;

const NO_LOSS: Completeness = {
  stored_spans: 900,
  losses: [{ source: "ingest", reason: "rejected", count: 0 }] as Completeness["losses"],
  completeness: "1",
  lower_bound: false,
};

/** A settled query result with `data`. */
function ok<T>(data: T) {
  return {
    data,
    isPending: false,
    isError: false,
    isSuccess: true,
    isPlaceholderData: false,
    refetch: vi.fn(),
  };
}

function queries(overrides: Partial<Record<keyof OverviewQueries, unknown>> = {}) {
  return {
    range: {
      value: "24h",
      from: "2026-10-20T12:00:00.000Z",
      to: "2026-10-21T12:00:00.000Z",
      durationMs: 24 * 3600_000,
    },
    environment: undefined,
    bucket: "hour",
    isLive: true,
    overview: ok({ current: KPIS, previous: KPIS, approximate: false }),
    monthToDate: ok({ current: KPIS, previous: KPIS, approximate: false }),
    timeseries: ok([]),
    models: ok([]),
    health: ok(HEALTH),
    detectorRuns: ok([]),
    openInsights: ok({ items: [], next_cursor: null }),
    completeness: ok(NO_LOSS),
    incidents: ok({ items: [], polling: "enabled", next_cursor: null }),
    releases: ok([]),
    alertRules: ok([]),
    budgets: ok([]),
    onboarding: ok({ has_traces: true }),
    ...overrides,
  } as unknown as OverviewQueries;
}

function renderInRouter(ui: ReactNode) {
  const rootRoute = createRootRoute({ component: () => <TooltipProvider>{ui}</TooltipProvider> });
  const anyPath = createRoute({ getParentRoute: () => rootRoute, path: "$" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([anyPath]),
    history: createMemoryHistory({ initialEntries: ["/o/p/overview"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

function stubTraces() {
  vi.spyOn(projectsApi, "traces").mockResolvedValue({ items: [], next_cursor: null });
}

const FIRING_RULE = {
  id: "r1",
  name: "Error rate above 5%",
  enabled: true,
  state: {
    state: "firing",
    since: "2026-10-21T11:48:00Z",
    last_value: null,
    last_evaluated_at: null,
  },
} as AlertRule;

const BUDGET = {
  id: "b1",
  name: "Monthly spend",
  scope: "project",
  scope_id: null,
  period: "monthly",
  amount_usd: "500",
  enabled: true,
  state: { spent_usd: "420", state: "ok" },
} as Budget;

describe("OverviewDashboard", () => {
  it("leads with four KPI links", async () => {
    stubTraces();
    renderInRouter(<OverviewDashboard queries={queries()} now={NOW} />);

    const ribbon = await screen.findByRole("list", { name: "Key metrics" });
    const links = within(ribbon).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/o/p/traces",
      "/o/p/traces?sort=duration",
      "/o/p/attribution",
      "/o/p/doctor",
    ]);
    expect(links[0]).toHaveAccessibleName(/Calls · error rate: 432/);
  });

  it("shows the Doctor's critical findings in danger text", async () => {
    stubTraces();
    const health = { ...HEALTH, open_critical: 1, open_warning: 2 };
    renderInRouter(<OverviewDashboard queries={queries({ health: ok(health) })} now={NOW} />);

    const ribbon = await screen.findByRole("list", { name: "Key metrics" });
    expect(within(ribbon).getByText("3 open findings", { exact: false })).toBeInTheDocument();
    expect(within(ribbon).getByText("1 critical")).toHaveClass("text-danger-text");
  });

  it("puts the Doctor's score components and formula in Needs attention's footer", async () => {
    stubTraces();
    const health = {
      ...HEALTH,
      components: [{ name: "errors", penalty: 12, observed: "0.0347", baseline: null }],
    };
    renderInRouter(<OverviewDashboard queries={queries({ health: ok(health) })} now={NOW} />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    for (const label of ["Findings", "Errors", "Latency", "Cost"]) {
      expect(within(card).getByText(label)).toBeInTheDocument();
    }
    expect(within(card).getByText("−12 of 30")).toBeInTheDocument();
    expect(
      within(card).getByRole("button", { name: "How the Doctor score is calculated" }),
    ).toBeInTheDocument();
  });

  it("says no call was priced instead of calling unknown spend a lower bound", async () => {
    stubTraces();
    const current = { ...KPIS, cost_usd: null, unpriced_calls: 432 };
    renderInRouter(
      <OverviewDashboard
        queries={queries({ overview: ok({ current, previous: KPIS, approximate: false }) })}
        now={NOW}
      />,
    );

    const card = await screen.findByRole("region", { name: "Spend by model" });
    expect(within(card).getByText("No priced calls in this window")).toBeInTheDocument();
    expect(within(card).queryByText(/lower bound/)).not.toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Set prices" })).toHaveAttribute(
      "href",
      "/o/p/pricing",
    );
  });

  it("reads the exact spend to screen readers, not only in the title", async () => {
    stubTraces();
    const current = { ...KPIS, cost_usd: "12345.678" };
    renderInRouter(
      <OverviewDashboard
        queries={queries({ overview: ok({ current, previous: KPIS, approximate: false }) })}
        now={NOW}
      />,
    );

    const ribbon = await screen.findByRole("list", { name: "Key metrics" });
    expect(within(ribbon).getAllByRole("link")[2]).toHaveAccessibleName(
      /\$12\.3K, exactly \$12,345\.68/,
    );
  });

  it("draws no empty footer in Needs attention before its lines load", async () => {
    stubTraces();
    const pending = { data: undefined, isPending: true, isError: false, refetch: vi.fn() };
    renderInRouter(
      <OverviewDashboard
        queries={queries({ health: pending, detectorRuns: pending, incidents: pending })}
        now={NOW}
      />,
    );

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(card.querySelector(".border-t")).toBeNull();
  });

  it("lists a firing alert and a budget at 84 %", async () => {
    stubTraces();
    renderInRouter(
      <OverviewDashboard
        queries={queries({ alertRules: ok([FIRING_RULE]), budgets: ok([BUDGET]) })}
        now={NOW}
      />,
    );

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(card).getByRole("link", { name: /Error rate above 5%/ })).toHaveAttribute(
      "href",
      "/o/p/alerts/r1",
    );
    expect(within(card).getByText("84% of $500.00 · monthly")).toBeInTheDocument();
  });

  it("says nothing needs attention when nothing does", async () => {
    stubTraces();
    renderInRouter(<OverviewDashboard queries={queries()} now={NOW} />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(card).getByText("Nothing needs attention.")).toBeInTheDocument();
  });

  it("folds telemetry completeness to one line when nothing was lost", async () => {
    stubTraces();
    renderInRouter(<OverviewDashboard queries={queries()} now={NOW} />);

    expect(await screen.findByText("No spans lost in this window.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Spans lost by source" })).not.toBeInTheDocument();
  });
});

describe("TracesTabsCard", () => {
  it("offers Recent errors, Slowest and Latest", async () => {
    stubTraces();
    renderInRouter(<TracesTabsCard now={NOW} />);
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Recent errors", "Slowest", "Latest"]);
  });

  it("fetches a tab's traces only once it is opened, the slowest by duration", async () => {
    const traces = vi
      .spyOn(projectsApi, "traces")
      .mockResolvedValue({ items: [], next_cursor: null });
    const user = userEvent.setup();
    renderInRouter(<TracesTabsCard now={NOW} />);

    await waitFor(() => {
      expect(traces).toHaveBeenCalledTimes(1);
    });
    expect(traces.mock.calls[0]?.[1]).toMatchObject({ status: "error" });

    await user.click(screen.getByRole("tab", { name: "Slowest" }));
    await waitFor(() => {
      expect(traces).toHaveBeenCalledTimes(2);
    });
    expect(traces.mock.calls[1]?.[1]).toMatchObject({ sort: "duration", limit: 5 });
    expect(traces.mock.calls[1]?.[1]).not.toHaveProperty("order");

    await user.click(screen.getByRole("tab", { name: "Latest" }));
    await waitFor(() => {
      expect(traces).toHaveBeenCalledTimes(3);
    });
    expect(traces.mock.calls[2]?.[1]).not.toHaveProperty("sort");
  });
});

describe("OverviewHero", () => {
  it("puts the greeting and the status on one heading line", async () => {
    renderInRouter(
      <OverviewHero
        eyebrow={{ full: "Wednesday, 21 October · All environments", compact: "21 Oct" }}
        greeting="Good evening, Smoke."
        status={{ kind: "findings", lead: "1 finding", accent: "needs a look." }}
        isLoading={false}
      />,
    );
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Good evening, Smoke. 1 finding needs a look.");
    expect(screen.getByRole("link", { name: /Explore traces/ })).toBeInTheDocument();
  });
});
