import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import {
  ApiError,
  attributionApi,
  billingSourcesApi,
  projectsApi,
  type BillingSource,
  type ReconciliationReport,
} from "@/lib/api";

import { response } from "../attribution-test-data";
import { CostStrip } from "./cost-strip";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "o", projectId: "p" }),
  useProjectFilters: () => ({
    range: {
      value: "7d",
      from: "2026-10-14T12:00:00.000Z",
      to: "2026-10-21T12:00:00.000Z",
      durationMs: 7 * 86_400_000,
    },
    environment: undefined,
  }),
}));

const SOURCE = {
  id: "s1",
  name: "OpenAI prod",
  projects: [{ id: "p", name: "App", slug: "app" }],
} as BillingSource;

const REPORT = {
  source: { id: "s1", provider: "openai", kind: "openai_costs_api" },
  window: { start: "2026-10-14T00:00:00Z", end: "2026-10-22T00:00:00Z" },
  projects: [{ id: "p", name: "App" }],
  days: [
    {
      day: "2026-10-19",
      provisional: false,
      provider_usd: "100.00",
      spanlight_usd: "103.20",
      explained_usd: "1.00",
      residual_usd: "-4.20",
    },
  ],
  summary: { days: 1, settled_days: 1, days_within_2pct: 0, max_abs_variance_ratio: null },
} as ReconciliationReport;

function renderStrip() {
  const rootRoute = createRootRoute({
    component: () => (
      <TooltipProvider>
        <CostStrip />
      </TooltipProvider>
    ),
  });
  const anyPath = createRoute({ getParentRoute: () => rootRoute, path: "$" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([anyPath]),
    history: createMemoryHistory({ initialEntries: ["/o/p/attribution"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(attributionApi, "read").mockResolvedValue(
    response({ totals: { ...response().totals, cost_usd: "375.05", unpriced_calls: 0 } }),
  );
  vi.spyOn(projectsApi, "tokenMix").mockResolvedValue({
    first_day: "2026-10-14",
    last_day: "2026-10-21",
    uncached_input_tokens: 6100,
    output_tokens: 2200,
    cache_read_tokens: 1500,
    cache_write_tokens: 200,
    fee_usd: "0",
    cost_usd: "375.05",
  });
  vi.spyOn(billingSourcesApi, "list").mockResolvedValue([SOURCE]);
});

describe("CostStrip", () => {
  it("renders four cards as links named by label and value", async () => {
    vi.spyOn(billingSourcesApi, "reconciliation").mockResolvedValue(REPORT);
    renderStrip();

    expect(
      await screen.findByRole("link", { name: /^Telemetry spend: \$375\.05/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: /^Provider billed: \$100\.00/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Variance: −4.2 %, within 10 %, open reconciliation" }),
    ).toHaveAttribute("href", "/o/p/reconciliation");
    expect(screen.getByRole("link", { name: /^Provider billed/ })).toHaveAttribute(
      "href",
      "/o/p/reconciliation",
    );
    expect(screen.getByRole("link", { name: /^Unpriced calls: 0/ })).toBeInTheDocument();
    expect(billingSourcesApi.reconciliation).toHaveBeenCalledWith("o", "s1", {
      from: "2026-10-14T00:00:00Z",
      to: "2026-10-22T00:00:00Z",
    });
  });

  it("labels the token mix bar with all four shares", async () => {
    vi.spyOn(billingSourcesApi, "reconciliation").mockResolvedValue(REPORT);
    renderStrip();

    expect(
      await screen.findByRole("img", {
        name: "Token mix: input 61 %, output 22 %, cache read 15 %, cache write 2 %",
      }),
    ).toBeInTheDocument();
  });

  it("shows a failed report as '—' on the two billing cards only", async () => {
    vi.spyOn(billingSourcesApi, "reconciliation").mockRejectedValue(new ApiError(500, {}));
    renderStrip();

    expect(await screen.findAllByRole("button", { name: "Retry" })).toHaveLength(2);
    expect(screen.getAllByText("Couldn't load billing data")).toHaveLength(2);
    expect(screen.getByRole("link", { name: /^Telemetry spend: \$375\.05/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Unpriced calls: 0/ })).toBeInTheDocument();
  });
});
