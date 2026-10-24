import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { ESTIMATE_METHOD, PlaygroundRunsLink, RunEstimateLine } from "./run-estimate-line";

function renderLine(estimate: Parameters<typeof RunEstimateLine>[0]["estimate"]) {
  render(
    <TooltipProvider>
      <RunEstimateLine estimate={estimate} />
    </TooltipProvider>,
  );
}

describe("RunEstimateLine", () => {
  it("says the ceiling at list prices, with the method behind a 24 px button", async () => {
    renderLine({ usd: "0.004" });
    expect(screen.getByText("Est. up to $0.004 at list prices")).toBeInTheDocument();
    const info = screen.getByRole("button", { name: "How the estimate works" });
    expect(info).toHaveClass("size-6");
    await userEvent.hover(info);
    expect((await screen.findAllByText(ESTIMATE_METHOD)).length).toBeGreaterThan(0);
    expect(ESTIMATE_METHOD).toContain("Org price overrides and cached-token discounts");
  });

  it("names the lettered column without a ceiling", () => {
    renderLine({ unknown: [{ column: 1, reason: "no_max_tokens", model: "gpt-4o" }] });
    expect(screen.getByText(/Est\. unavailable for Run B/)).toHaveTextContent(
      "Est. unavailable for Run B (no max tokens set)",
    );
  });

  it("names the model without a price, and never shows $0", () => {
    renderLine({ unknown: [{ column: 1, reason: "no_price", model: "my-model" }] });
    const line = screen.getByText(/Est\. unavailable for Run B/);
    expect(line).toHaveTextContent("Est. unavailable for Run B (no price for my-model)");
    expect(screen.getByText("my-model").tagName).toBe("CODE");
    expect(line).not.toHaveTextContent("$0");
  });
});

describe("PlaygroundRunsLink", () => {
  it("opens the traces the playground produced", async () => {
    const rootRoute = createRootRoute({
      component: () => <PlaygroundRunsLink orgId="o" projectId="p" />,
    });
    const anyPath = createRoute({ getParentRoute: () => rootRoute, path: "$" });
    const router = createRouter({
      routeTree: rootRoute.addChildren([anyPath]),
      history: createMemoryHistory({ initialEntries: ["/o/p/playground"] }),
    });
    // The test tree is not the registered app router; the link only needs a location.
    render(<RouterProvider router={router as never} />);
    const link = await screen.findByRole("link", { name: "View playground runs" });
    expect(link).toHaveAttribute("href", "/o/p/traces?tag=spanlight%3Aplayground");
  });
});
