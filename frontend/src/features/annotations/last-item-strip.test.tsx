import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  annotationsApi,
  projectsApi,
  scoresApi,
  type AnnotationQueue,
  type QueueItem,
  type Score,
  type ScoreConfig,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/annotate/queue_1`;
const ZERO = { pending: 0, done: 0, skipped: 0 };

const CONFIG: ScoreConfig = {
  id: "cfg_format",
  name: "format",
  data_type: "boolean",
  min_value: null,
  max_value: null,
  categories: [],
  description: null,
  implicit: false,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const QUEUE: AnnotationQueue = {
  id: "queue_1",
  name: "Support triage",
  instructions: null,
  score_config_ids: [CONFIG.id],
  progress: {
    pending: 2,
    done: 0,
    skipped: 0,
    by_reason: {
      manual: { pending: 2, done: 0, skipped: 0 },
      doctor_finding: ZERO,
      error: ZERO,
      cost_outlier: ZERO,
      random: ZERO,
    },
  },
  created_at: "2026-10-01T00:00:00Z",
};

function item(id: string): QueueItem {
  return {
    id,
    trace_id: `trace_${id}`,
    span_id: null,
    status: "pending",
    assigned_to: "u1",
    assigned_at: "2026-10-21T00:00:00Z",
    completed_by: null,
    completed_at: null,
    created_at: "2026-10-21T00:00:00Z",
  };
}

function evalScore(traceId: string, value: number): Score {
  return {
    id: `score_${traceId}`,
    trace_id: traceId,
    span_id: null,
    name: "format",
    data_type: "boolean",
    value,
    string_value: null,
    source: "eval",
    comment: null,
    author_user_id: null,
    author_name: null,
    evaluator_run_id: "run_1",
    created_at: "2026-10-21T00:00:00Z",
  };
}

describe("Last item strip", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(annotationsApi, "get").mockResolvedValue(QUEUE);
    vi.spyOn(annotationsApi, "list").mockResolvedValue({ items: [], next_cursor: null });
    // Work mode ignores the item these answer with.
    vi.spyOn(annotationsApi, "complete").mockResolvedValue(item("a"));
    vi.spyOn(annotationsApi, "release").mockResolvedValue(item("a"));
    vi.spyOn(annotationsApi, "claim")
      .mockResolvedValueOnce(item("a"))
      .mockResolvedValueOnce(item("b"))
      .mockResolvedValue(undefined);
    vi.spyOn(scoresApi, "configs").mockResolvedValue([CONFIG]);
    vi.spyOn(projectsApi, "trace").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(scoresApi, "forTrace").mockImplementation((_projectId, traceId) =>
      Promise.resolve(traceId === "trace_a" ? [evalScore("trace_a", 1)] : [evalScore(traceId, 0)]),
    );
  });

  it("stays hidden before a submit, then compares the last item and follows to All done", async () => {
    const user = userEvent.setup();
    renderApp(PATH);
    const scores = await screen.findByRole("region", { name: "Scores" });
    await within(scores).findByText("format");
    expect(screen.queryByText("Last item")).not.toBeInTheDocument();
    expect(scoresApi.forTrace).not.toHaveBeenCalled();

    // Yes, then Submit & next: the evaluator also said yes.
    await user.keyboard("1");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(annotationsApi.claim).toHaveBeenCalledTimes(2));
    const strip = await within(await screen.findByRole("region", { name: "Scores" })).findByRole(
      "status",
    );
    expect(strip).toHaveTextContent("Last item");
    expect(await within(strip).findByText("Agree")).toBeInTheDocument();
    expect(strip).toHaveTextContent("format · you Yes · evaluator Yes");
    expect(strip.querySelector("svg")).not.toBeNull();
    expect(within(strip).getByRole("link", { name: "Open trace" })).toHaveAttribute(
      "href",
      "/acme/checkout/traces/trace_a",
    );

    // The final item: the evaluator said no, and the strip moves to the All done card.
    await user.keyboard("1");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "All done" })).toBeInTheDocument();
    const last = screen.getByText("Last item").closest<HTMLElement>('[role="status"]');
    if (last === null) {
      throw new Error("The Last item strip is not a status region");
    }
    expect(await within(last).findByText("Disagree")).toBeInTheDocument();
    expect(last).toHaveTextContent("format · you Yes · evaluator No");
    expect(scoresApi.forTrace).toHaveBeenLastCalledWith("proj_1", "trace_b");
  });

  it("explains a score the reviewer did not set", async () => {
    // A score added to the queue after the submit: the strip lists it, but the reviewer gave none.
    const TONE: ScoreConfig = { ...CONFIG, id: "cfg_tone", name: "tone", data_type: "categorical" };
    let added = false;
    vi.mocked(annotationsApi.get).mockImplementation(() =>
      Promise.resolve(added ? { ...QUEUE, score_config_ids: [CONFIG.id, TONE.id] } : QUEUE),
    );
    vi.mocked(scoresApi.configs).mockResolvedValue([CONFIG, TONE]);
    const user = userEvent.setup();
    renderApp(PATH);
    await within(await screen.findByRole("region", { name: "Scores" })).findByText("format");

    await user.keyboard("1");
    added = true;
    await user.keyboard("{Enter}");

    const strip = await within(await screen.findByRole("region", { name: "Scores" })).findByRole(
      "status",
    );
    expect(await within(strip).findByText(/^tone · you/)).toBeInTheDocument();
    expect(within(strip).getByLabelText("Unknown: You didn't score this.")).toHaveTextContent("—");
  });

  it("says so when the evaluator scores cannot be loaded", async () => {
    // A 4xx is not retried, so the error shows at once.
    vi.mocked(scoresApi.forTrace).mockRejectedValue(new ApiError(404, {}));
    const user = userEvent.setup();
    renderApp(PATH);
    await within(await screen.findByRole("region", { name: "Scores" })).findByText("format");

    await user.keyboard("1");
    await user.keyboard("{Enter}");

    expect(await screen.findByText("Couldn't load evaluator scores.")).toBeInTheDocument();
  });
});
