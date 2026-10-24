import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { KpiCard } from "./kpi-card";

function renderInRouter(ui: ReactNode) {
  const rootRoute = createRootRoute({
    component: () => <TooltipProvider>{ui}</TooltipProvider>,
  });
  const anyPath = createRoute({ getParentRoute: () => rootRoute, path: "$" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([anyPath]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(<RouterProvider router={router as never} />);
}

describe("KpiCard", () => {
  it("renders an unknown value as a dash with its reason", async () => {
    renderInRouter(<KpiCard label="Spend" value={null} unknownReason="No price for this model" />);
    expect(await screen.findByLabelText("Unknown: No price for this model")).toHaveTextContent("—");
  });

  it("is one link named by its label and value when it has an href", async () => {
    renderInRouter(
      <KpiCard
        label="Error rate"
        value="3.5%"
        denominator="15 of 432 calls"
        href={{
          to: "/$orgId/$projectId/traces",
          params: { orgId: "o", projectId: "p" },
          search: { status: "error" },
        }}
      />,
    );
    const link = await screen.findByRole("link", { name: /Error rate: 3.5%/ });
    expect(link).toHaveAttribute("href", "/o/p/traces?status=error");
    expect(screen.getByText("15 of 432 calls")).toBeInTheDocument();
  });
});
