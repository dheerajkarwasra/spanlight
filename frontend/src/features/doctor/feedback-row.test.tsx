import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, insightsApi, type InsightFeedback } from "@/lib/api";

import { FeedbackRow } from "./feedback-row";

vi.mock("@/features/shell/project-context", () => ({
  useProjectParams: () => ({ orgId: "org_1", projectId: "proj_1" }),
}));

const NONE: InsightFeedback = { mine: null, right: 2, wrong: 0 };
// This year's 14 Oct, so the label never needs the year whenever the suite runs.
const VOTED_AT = `${String(new Date().getFullYear())}-10-14T12:00:00Z`;

function renderRow(feedback: InsightFeedback = NONE, canVote = true) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FeedbackRow insightId="ins_1" feedback={feedback} canVote={canVote} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FeedbackRow", () => {
  it("asks the question with two unpressed buttons", () => {
    renderRow();
    expect(screen.getByText("Was this right?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Right" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Wrong" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByLabelText("Add a note (optional)")).not.toBeInTheDocument();
  });

  it("saves a vote at once, then opens the note field with focus in it", async () => {
    const put = vi
      .spyOn(insightsApi, "putFeedback")
      .mockResolvedValue({ verdict: "wrong", note: null, updated_at: "2026-10-14T12:00:00Z" });
    renderRow();
    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));
    expect(put).toHaveBeenCalledWith("proj_1", "ins_1", { verdict: "wrong" });
    const note = await screen.findByLabelText("Add a note (optional)");
    expect(note).toHaveFocus();
    expect(note).toHaveAttribute("maxlength", "500");
  });

  it("sends the note with the current verdict", async () => {
    const put = vi
      .spyOn(insightsApi, "putFeedback")
      .mockResolvedValue({ verdict: "wrong", note: null, updated_at: "2026-10-14T12:00:00Z" });
    renderRow();
    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));
    await userEvent.type(await screen.findByLabelText("Add a note (optional)"), "Our proxy");
    expect(screen.getByText("9 / 500")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save note" }));
    expect(put).toHaveBeenLastCalledWith("proj_1", "ins_1", {
      verdict: "wrong",
      note: "Our proxy",
    });
    await waitFor(() => {
      expect(screen.queryByLabelText("Add a note (optional)")).not.toBeInTheDocument();
    });
  });

  it("announces only the caller's own vote, never the other members' counts", () => {
    renderRow();
    const counts = screen.getByText("2 said right · 0 said wrong.");
    expect(counts.closest("[aria-live]")).toBeNull();
    expect(screen.getByText("Your answer measures how often this check is right.")).toHaveAttribute(
      "aria-live",
      "polite",
    );
  });

  it("marks the current verdict pressed and offers to remove it", () => {
    renderRow({ mine: { verdict: "right", note: null, updated_at: VOTED_AT }, right: 3, wrong: 0 });
    expect(screen.getByRole("button", { name: "Right" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Wrong" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("You said this was right on 14 Oct")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove my answer" })).toBeInTheDocument();
  });

  it("removes the vote when the pressed verdict is clicked again", async () => {
    const remove = vi.spyOn(insightsApi, "deleteFeedback").mockResolvedValue(undefined);
    const put = vi.spyOn(insightsApi, "putFeedback");
    renderRow({ mine: { verdict: "right", note: null, updated_at: VOTED_AT }, right: 3, wrong: 0 });
    await userEvent.click(screen.getByRole("button", { name: "Right" }));
    expect(remove).toHaveBeenCalledWith("proj_1", "ins_1");
    expect(put).not.toHaveBeenCalled();
  });

  it("is disabled for a viewer, with the reason as visible text", async () => {
    const put = vi.spyOn(insightsApi, "putFeedback");
    renderRow(NONE, false);
    const reason = "Only members, admins and owners can give feedback";
    expect(screen.getByText(reason)).toBeVisible();
    for (const name of ["Right", "Wrong"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(button).toHaveAccessibleDescription(reason);
      await userEvent.click(button);
    }
    expect(put).not.toHaveBeenCalled();
    expect(screen.getByText("2 said right · 0 said wrong.")).toBeInTheDocument();
  });

  it("shows a failed save inline and keeps the previous verdict pressed", async () => {
    vi.spyOn(insightsApi, "putFeedback").mockRejectedValue(
      new ApiError(503, { title: "Service unavailable", status: 503 }),
    );
    renderRow({ mine: { verdict: "right", note: null, updated_at: VOTED_AT }, right: 3, wrong: 0 });
    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your answer wasn’t saved: the server answered 503. Try again.",
    );
    expect(screen.getByRole("button", { name: "Right" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByLabelText("Add a note (optional)")).not.toBeInTheDocument();
  });
});
