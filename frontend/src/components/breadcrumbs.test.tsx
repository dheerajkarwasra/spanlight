import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { Breadcrumbs, Crumb } from "./breadcrumbs";

/** Renders `ui` under a real router whose location is `path`, so link active state is real. */
async function renderAt(ui: ReactNode, path = "/o/p/traces/abc") {
  const rootRoute = createRootRoute({ component: () => ui });
  const anyPath = createRoute({ getParentRoute: () => rootRoute, path: "$" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([anyPath]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  // The test tree is not the registered app router; the crumbs only need a location.
  render(<RouterProvider router={router as never} />);
  return screen.findByRole("navigation", { name: "Breadcrumb" });
}

describe("Breadcrumbs", () => {
  it("lists the parent links, then the current page as text", async () => {
    await renderAt(
      <Breadcrumbs current="answer_ticket">
        <Crumb to="/$orgId/$projectId/doctor" params={{ orgId: "o", projectId: "p" }}>
          Doctor
        </Crumb>
      </Breadcrumbs>,
    );

    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    const items = within(within(nav).getByRole("list")).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["Doctor", "answer_ticket"]);
    expect(
      within(nav)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Doctor"]);

    const current = within(nav).getByText("answer_ticket");
    expect(current).toHaveAttribute("aria-current", "page");
    expect(current).toHaveAttribute("title", "answer_ticket");
    expect(current.closest("a")).toBeNull();
    // The chevrons are decoration: the list reads "Doctor, answer_ticket".
    expect(nav.querySelectorAll("svg:not([aria-hidden='true'])")).toHaveLength(0);
    expect(nav.querySelectorAll("svg")).toHaveLength(1);
  });

  it("renders no item for a null or false crumb", async () => {
    await renderAt(
      <Breadcrumbs current="Run 3">
        {null}
        <Crumb to="/$orgId/$projectId/experiments" params={{ orgId: "o", projectId: "p" }}>
          Experiments
        </Crumb>
        {false}
      </Breadcrumbs>,
    );

    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["Experiments", "Run 3"]);
    expect(screen.getByRole("navigation").querySelectorAll("svg")).toHaveLength(1);
  });

  it("gives every crumb link a 44 px touch target that shrinks to 24 px from sm", async () => {
    await renderAt(
      <Breadcrumbs current="Run 3">
        <Crumb to="/$orgId/$projectId/experiments" params={{ orgId: "o", projectId: "p" }}>
          Experiments
        </Crumb>
        <Crumb to="/$orgId/$projectId/experiments" params={{ orgId: "o", projectId: "p" }}>
          Nightly
        </Crumb>
      </Breadcrumbs>,
    );

    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["Experiments", "Nightly"]);
    for (const link of links) {
      expect(link).toHaveClass("min-h-11", "sm:min-h-6");
    }
  });

  it("never marks a parent crumb as the current page, even on a page under it", async () => {
    const nav = await renderAt(
      <Breadcrumbs current="answer_ticket">
        <Crumb to="/$orgId/$projectId/traces" params={{ orgId: "o", projectId: "p" }}>
          Traces
        </Crumb>
      </Breadcrumbs>,
      "/o/p/traces/abc",
    );

    const link = within(nav).getByRole("link", { name: "Traces" });
    expect(link).toHaveAttribute("href", "/o/p/traces");
    expect(link).not.toHaveAttribute("aria-current");
    expect(link).not.toHaveAttribute("data-status");
    expect(link).not.toHaveClass("active");
    const current = nav.querySelectorAll("[aria-current]");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("answer_ticket");
  });
});
