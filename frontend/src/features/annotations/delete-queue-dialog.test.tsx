import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, annotationsApi, authApi, scoresApi, type AnnotationQueue } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/queues/queue_1`;

const ZERO = { pending: 0, done: 0, skipped: 0 };

const QUEUE: AnnotationQueue = {
  id: "queue_1",
  name: "Support triage",
  instructions: null,
  score_config_ids: [],
  progress: {
    pending: 40,
    done: 6,
    skipped: 2,
    by_reason: {
      manual: { pending: 40, done: 6, skipped: 2 },
      doctor_finding: ZERO,
      error: ZERO,
      cost_outlier: ZERO,
      random: ZERO,
    },
  },
  created_at: "2026-10-01T00:00:00Z",
};

describe("Delete queue", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(annotationsApi, "get").mockResolvedValue(QUEUE);
    vi.spyOn(annotationsApi, "items").mockResolvedValue({ items: [], next_cursor: null });
    vi.spyOn(annotationsApi, "list").mockResolvedValue({ items: [], next_cursor: null });
    vi.spyOn(scoresApi, "configs").mockResolvedValue([]);
  });

  it("confirms with the real item count, deletes, then opens the queue list", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(annotationsApi, "delete").mockResolvedValue(undefined);
    const router = renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: "Delete queue" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Delete queue “Support triage”?",
    });
    expect(dialog).toHaveTextContent(
      "Its 48 items leave review. Scores already saved stay on their traces. This can't be undone.",
    );
    expect(remove).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Delete queue" }));
    expect(remove).toHaveBeenCalledWith("proj_1", "queue_1");
    await waitFor(() => {
      expect(router.state.location.pathname).toMatch(/\/queues$/);
    });
  });

  it("shows the refusal while a review policy fills the queue", async () => {
    const user = userEvent.setup();
    vi.spyOn(annotationsApi, "delete").mockRejectedValue(
      new ApiError(409, {
        code: "QUEUE_IN_USE",
        detail:
          "A review policy fills this queue. Delete the policy or point it at another queue first.",
      }),
    );
    renderApp(PATH);

    await user.click(await screen.findByRole("button", { name: "Delete queue" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete queue" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "A review policy fills this queue.",
    );
    expect(within(dialog).getByRole("button", { name: "Delete queue" })).toBeDisabled();
  });

  it("offers no delete to a viewer", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: ME.memberships.map((membership) => ({ ...membership, role: "viewer" })),
    });
    renderApp(PATH);

    expect(await screen.findByRole("heading", { name: "Support triage" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete queue" })).not.toBeInTheDocument();
  });
});
