import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
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

vi.mock("./refused-spans-notice", () => ({ RefusedSpansNotice: () => null }));
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
    timeseries: ok({ items: [] }),
    models: ok({ items: [] }),
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

  it("shows Doctor health unavailable and retries only health", async () => {
    stubTraces();
    const user = userEvent.setup();
    const health = {
      data: undefined,
      isPending: false,
      isError: true,
      isFetching: false,
      refetch: vi.fn(),
    };
    const others = { openInsights: ok({ items: [], next_cursor: null }), budgets: ok([]) };
    renderInRouter(<OverviewDashboard queries={queries({ health, ...others })} now={NOW} />);

    const ribbon = await screen.findByRole("list", { name: "Key metrics" });
    const doctor = within(ribbon).getAllByRole("link")[3];
    expect(doctor).toHaveAccessibleName(/Doctor: Unknown: The Doctor's score didn't load/);
    // Beside the dash from `sm` up (817:2317), under it on phones (817:2676).
    expect(within(ribbon).getAllByText("Unavailable")).toHaveLength(2);
    expect(within(doctor as HTMLElement).queryByText("/100")).not.toBeInTheDocument();
    expect(within(ribbon).getAllByRole("link")[0]).toHaveAccessibleName(/432/);

    const card = screen.getByRole("region", { name: "Needs attention" });
    const alert = within(card).getByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load Doctor health");
    expect(alert).toHaveTextContent("The Doctor's score didn't load");
    expect(within(card).queryByText("Nothing needs attention.")).not.toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(health.refetch).toHaveBeenCalledTimes(1);
    expect(others.budgets.refetch).not.toHaveBeenCalled();
  });

  it("keeps focus on the card when the health retry succeeds", async () => {
    stubTraces();
    const user = userEvent.setup();
    function RetryThenLoad() {
      const [health, setHealth] = useState<unknown>({
        data: undefined,
        isPending: false,
        isError: true,
        isFetching: false,
        refetch: () => {
          setHealth(ok(HEALTH));
        },
      });
      return <OverviewDashboard queries={queries({ health })} now={NOW} />;
    }
    renderInRouter(<RetryThenLoad />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    await user.click(within(card).getByRole("button", { name: "Try again" }));

    expect(within(card).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(within(card).getByRole("heading", { name: "Needs attention" })).toHaveFocus();
  });

  it("leaves focus the user moved while the health retry was in flight", async () => {
    stubTraces();
    const user = userEvent.setup();
    const failedHealth = {
      data: undefined,
      isPending: false,
      isError: true,
      isFetching: false,
    };
    let land: () => void = () => undefined;
    function RetryInFlight() {
      const [health, setHealth] = useState<unknown>({
        ...failedHealth,
        refetch: () => {
          setHealth({ ...failedHealth, isFetching: true, refetch: vi.fn() });
          land = () => {
            setHealth(ok(HEALTH));
          };
        },
      });
      return <OverviewDashboard queries={queries({ health })} now={NOW} />;
    }
    renderInRouter(<RetryInFlight />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    await user.click(within(card).getByRole("button", { name: "Try again" }));
    const elsewhere = within(screen.getByRole("list", { name: "Key metrics" })).getAllByRole(
      "link",
    )[0] as HTMLElement;
    elsewhere.focus();
    act(() => {
      land();
    });

    expect(within(card).queryByRole("button", { name: /Try again/ })).not.toBeInTheDocument();
    expect(elsewhere).toHaveFocus();
  });

  it("keeps focus on the card when the findings, alerts or budgets retry succeeds", async () => {
    stubTraces();
    const user = userEvent.setup();
    function RetryThenLoad() {
      const [budgets, setBudgets] = useState<unknown>({
        data: undefined,
        isPending: false,
        isError: true,
        isFetching: false,
        refetch: () => {
          setBudgets(ok([]));
        },
      });
      return <OverviewDashboard queries={queries({ budgets })} now={NOW} />;
    }
    renderInRouter(<RetryThenLoad />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    await user.click(within(card).getByRole("button", { name: "Try again" }));

    expect(within(card).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(within(card).getByRole("heading", { name: "Needs attention" })).toHaveFocus();
  });

  it("returns lost focus to Try again when the retry fails again", async () => {
    stubTraces();
    const user = userEvent.setup();
    const failedBudgets = { data: undefined, isPending: false, isError: true };
    let land: () => void = () => undefined;
    function RetryFails() {
      const [budgets, setBudgets] = useState<unknown>({
        ...failedBudgets,
        isFetching: false,
        refetch: () => {
          setBudgets({ ...failedBudgets, isFetching: true, refetch: vi.fn() });
          land = () => {
            setBudgets({ ...failedBudgets, isFetching: false, refetch: vi.fn() });
          };
        },
      });
      return <OverviewDashboard queries={queries({ budgets })} now={NOW} />;
    }
    renderInRouter(<RetryFails />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    const retry = within(card).getByRole("button", { name: "Try again" });
    await user.click(retry);
    // A browser can drop focus to the page while the request is in flight.
    retry.blur();
    act(() => {
      land();
    });

    expect(within(card).getByRole("button", { name: "Try again" })).toHaveFocus();
  });

  it("disables the health retry while it is in flight", async () => {
    stubTraces();
    const health = {
      data: undefined,
      isPending: false,
      isError: true,
      isFetching: true,
      refetch: vi.fn(),
    };
    renderInRouter(<OverviewDashboard queries={queries({ health })} now={NOW} />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(card).getByRole("button", { name: /Try again/ })).toBeDisabled();
  });

  it("hides a cached Doctor score after its refresh failed", async () => {
    stubTraces();
    const health = {
      data: HEALTH,
      isPending: false,
      isError: true,
      isFetching: false,
      refetch: vi.fn(),
    };
    renderInRouter(<OverviewDashboard queries={queries({ health })} now={NOW} />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(card).queryByText("Findings")).not.toBeInTheDocument();
    expect(within(card).getByRole("alert")).toHaveTextContent("Couldn't load Doctor health");
    const ribbon = screen.getByRole("list", { name: "Key metrics" });
    expect(within(ribbon).queryByText("72")).not.toBeInTheDocument();
  });

  it("keeps a successful no-score response without the failed-request retry", async () => {
    stubTraces();
    const health = ok({ ...HEALTH, value: null });
    renderInRouter(<OverviewDashboard queries={queries({ health })} now={NOW} />);

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(card).queryByText("Couldn't load Doctor health")).not.toBeInTheDocument();
    expect(screen.queryByText("Unavailable")).not.toBeInTheDocument();
  });

  it("says source history is unavailable instead of no calls on unavailable empty lists", async () => {
    stubTraces();
    const unavailable = ok({ items: [], aggregate_status: "unavailable" });
    renderInRouter(
      <OverviewDashboard
        queries={queries({ models: unavailable, timeseries: unavailable })}
        now={NOW}
      />,
    );

    const models = await screen.findByRole("heading", { name: "Source history unavailable" });
    expect(models).toBeInTheDocument();
    expect(screen.queryByText("No model calls in this period")).not.toBeInTheDocument();
    expect(screen.queryByText("No LLM calls in this period")).not.toBeInTheDocument();
    // The Calls & errors totals: unknown, not 0.
    expect(screen.queryByText(/^0 (calls|errors)$/)).not.toBeInTheDocument();
    expect(
      screen.getAllByLabelText(
        "Unknown: Source rows were removed. Stored values are snapshots; missing values are unknown.",
      ),
    ).toHaveLength(2);
    expect(
      screen.getAllByText(
        "Source rows were removed. Stored values are snapshots; missing values are unknown.",
      ).length,
    ).toBeGreaterThan(0);
  });

  it("keeps the no-calls copy for complete empty lists", async () => {
    stubTraces();
    const complete = ok({ items: [], aggregate_status: "complete" });
    renderInRouter(
      <OverviewDashboard queries={queries({ models: complete, timeseries: complete })} now={NOW} />,
    );

    expect(await screen.findByText("No model calls in this period")).toBeInTheDocument();
    expect(screen.queryByText("Source history unavailable")).not.toBeInTheDocument();
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
