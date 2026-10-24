import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { projectsApi } from "@/lib/api";
import { readLastProject } from "@/lib/last-project";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const workMode = vi.hoisted(() => ({ mounts: 0 }));

// Work mode claims a review item when it mounts, so it must mount once (finding T9-03).
vi.mock("@/features/annotations/annotate-page", async () => {
  const { useEffect } = await import("react");
  return {
    AnnotatePage: function AnnotatePage() {
      useEffect(() => {
        workMode.mounts += 1;
      }, []);
      return <p>Work mode</p>;
    },
  };
});

vi.mock("@/features/billing-sources/reconciliation-page", () => ({
  ReconciliationPage: () => <p>Reconciliation page</p>,
}));

/** A window at least 1280 px wide (Tailwind `xl`), where the rail is expanded unless collapsed. */
function wideWindow() {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query === "(min-width: 80rem)",
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

async function expectLastProject(projectId: string) {
  await waitFor(() => {
    expect(readLastProject()).toEqual({ orgId: ORG_ID, projectId });
  });
}

describe("AppShell", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("remembers every project it opens, so / can return to it", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout"), makeProject("proj_2", "Search")]);
    const router = renderApp(`/${ORG_ID}/proj_1/settings/project`);
    await expectLastProject("proj_1");

    const switchTo = (projectId: string) =>
      act(() =>
        router.navigate({
          to: "/$orgId/$projectId/settings/project",
          params: { orgId: ORG_ID, projectId },
        }),
      );

    await switchTo("proj_2");
    await expectLastProject("proj_2");

    // Back to a project that is already cached: it loads instantly, and still counts as a visit.
    await switchTo("proj_1");
    await expectLastProject("proj_1");
  });

  it("doesn't remember a project that doesn't exist", async () => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    renderApp(`/${ORG_ID}/missing/settings/project`);

    expect(await screen.findByText("Page not found")).toBeInTheDocument();
    expect(readLastProject()).toBeNull();
  });

  it("mounts the work-mode page once when entering it from a chromed page", async () => {
    workMode.mounts = 0;
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    const router = renderApp(`/${ORG_ID}/proj_1/settings/project`);
    await expectLastProject("proj_1");
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();

    await act(() =>
      router.navigate({
        to: "/$orgId/$projectId/annotate/$queueId",
        params: { orgId: ORG_ID, projectId: "proj_1", queueId: "queue_1" },
      }),
    );

    expect(await screen.findByText("Work mode")).toBeInTheDocument();
    // The rail belongs to the chromed layout only.
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
    expect(workMode.mounts).toBe(1);
  });

  it("truncates a long project name in the phone app bar, beside the search and menu buttons", async () => {
    const longName = "A very long project name for the phone header truncation chk";
    mockSignedIn([{ ...makeProject("proj_1", longName), slug: "long-name" }]);
    renderApp(`/${ORG_ID}/proj_1/settings/project`);

    const bar = await screen.findByRole("banner");
    expect(bar).toHaveAttribute("data-app-bar");
    const name = await within(bar).findByText(longName);
    expect(name).toHaveClass("min-w-0", "truncate");
    expect(name).toHaveAttribute("title", longName);
    // Stretched, not `items-start`: a fit-content row would size to the whole name and overflow.
    const trigger = within(bar).getByRole("button", { name: new RegExp(`^Project ${longName}`) });
    expect(trigger).toHaveClass("min-w-0", "items-stretch");
    expect(trigger).not.toHaveClass("items-start");
    // The buttons sit in their own non-shrinking group, a sibling of the switcher's group.
    const buttons = within(bar).getByRole("button", { name: "Search" }).parentElement;
    expect(buttons).toHaveClass("shrink-0");
    for (const button of within(buttons as HTMLElement).getAllByRole("button")) {
      expect(button).toHaveClass("size-11");
    }
    expect(buttons?.parentElement).toBe(bar);
    expect(within(bar).getByRole("button", { name: "Open navigation" }).parentElement).toBe(
      buttons,
    );
    expect(trigger.parentElement).toHaveClass("min-w-0");
    expect(trigger.parentElement?.parentElement).toBe(bar);
  });

  describe("rail", () => {
    it("collapses to the icon rail and remembers it", async () => {
      wideWindow();
      mockSignedIn([makeProject("proj_1", "Checkout")]);
      renderApp(`/${ORG_ID}/proj_1/settings/project`);

      await userEvent.click(await screen.findByRole("button", { name: "Collapse sidebar" }));

      expect(window.localStorage.getItem("spanlight.rail")).toBe("collapsed");
      // At this width it expands in place, so it opens no dialog.
      expect(screen.getByRole("button", { name: "Expand sidebar" })).not.toHaveAttribute(
        "aria-haspopup",
      );
      expect(screen.queryByRole("button", { name: "Collapse sidebar" })).toBeNull();
    });

    it("still collapses for the session when storage is blocked", async () => {
      wideWindow();
      mockSignedIn([makeProject("proj_1", "Checkout")]);
      renderApp(`/${ORG_ID}/proj_1/settings/project`);
      const collapse = await screen.findByRole("button", { name: "Collapse sidebar" });
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("Blocked", "SecurityError");
      });

      await userEvent.click(collapse);

      expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    });

    it("shows the Connect tile only before the first trace", async () => {
      wideWindow();
      mockSignedIn([makeProject("proj_1", "Checkout")]);
      vi.spyOn(projectsApi, "onboarding").mockResolvedValue({
        has_traces: false,
        first_trace_at: null,
      });
      renderApp(`/${ORG_ID}/proj_1/settings/project`);

      const nav = await screen.findByRole("navigation", { name: "Main" });
      const rail = nav.closest("aside");
      expect(rail).not.toBeNull();
      expect(
        await within(rail as HTMLElement).findByRole("link", { name: /Connect your app/ }),
      ).toBeInTheDocument();
    });

    it("drops the Connect tile once traces arrive, and the user menu still offers it", async () => {
      wideWindow();
      mockSignedIn([makeProject("proj_1", "Checkout")]);
      vi.spyOn(projectsApi, "onboarding").mockResolvedValue({
        has_traces: true,
        first_trace_at: "2026-10-01T00:00:00Z",
      });
      renderApp(`/${ORG_ID}/proj_1/settings/project`);
      await waitFor(() => {
        expect(projectsApi.onboarding).toHaveBeenCalledWith("proj_1");
      });

      const nav = await screen.findByRole("navigation", { name: "Main" });
      const rail = nav.closest("aside") as HTMLElement;
      expect(within(rail).queryByRole("link", { name: /Connect your app/ })).toBeNull();

      await userEvent.click(within(rail).getByRole("button", { name: /Account menu/ }));
      expect(await screen.findByRole("menuitem", { name: "Connect your app" })).toBeInTheDocument();
    });

    it("lights Cost, not Settings, on a billing source's reconciliation page", async () => {
      wideWindow();
      mockSignedIn([makeProject("proj_1", "Checkout")]);
      renderApp(`/${ORG_ID}/proj_1/settings/billing-sources/s1`);
      await screen.findByText("Reconciliation page");

      const nav = screen.getByRole("navigation", { name: "Main" });
      expect(within(nav).getByRole("link", { name: "Cost" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      expect(within(nav).getByRole("link", { name: "Settings" })).not.toHaveAttribute(
        "aria-current",
      );
    });
  });
});
