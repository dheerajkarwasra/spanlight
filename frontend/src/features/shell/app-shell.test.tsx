import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
});
