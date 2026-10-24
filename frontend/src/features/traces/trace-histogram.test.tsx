import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, projectsApi } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PROJECT_ID = "proj_1";
const WEEK =
  "range=custom&from=2026-10-12T00:00:00.000Z&to=2026-10-19T00:00:00.000Z&model=gpt-4.1&sort=cost";
const LIST = `/${ORG_ID}/${PROJECT_ID}/traces?${WEEK}`;

beforeEach(() => {
  mockSignedIn([makeProject(PROJECT_ID, "Support")]);
  vi.spyOn(projectsApi, "traces").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(projectsApi, "overview").mockRejectedValue(new ApiError(500, {}));
});

describe("the traces histogram", () => {
  it("asks with the list's filters and narrows the window to a clicked day", async () => {
    const histogram = vi.spyOn(projectsApi, "traceHistogram").mockResolvedValue({
      bucket: "day",
      buckets: [{ bucket_start: "2026-10-14T00:00:00Z", traces: 132, errors: 3 }],
    });
    const router = renderApp(LIST);

    const group = await screen.findByRole("group", { name: "Traces over time" });
    // Seven days, the empty ones filled in.
    expect(within(group).getAllByRole("button")).toHaveLength(7);
    const [, query] = histogram.mock.calls[0] ?? [];
    expect(query).toMatchObject({ model: "gpt-4.1", from: "2026-10-12T00:00:00.000Z" });
    expect(query).not.toHaveProperty("sort");
    expect(query).not.toHaveProperty("limit");

    await userEvent.click(
      within(group).getByRole("button", { name: "Oct 14 · 132 traces · 3 errors" }),
    );
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({
        range: "custom",
        from: "2026-10-14T00:00:00.000Z",
        to: "2026-10-15T00:00:00.000Z",
        model: "gpt-4.1",
        sort: "cost",
      });
    });
  });

  it("is one tab stop that arrow keys move along", async () => {
    vi.spyOn(projectsApi, "traceHistogram").mockResolvedValue({
      bucket: "day",
      buckets: [{ bucket_start: "2026-10-18T00:00:00Z", traces: 2, errors: 0 }],
    });
    renderApp(LIST);

    const group = await screen.findByRole("group", { name: "Traces over time" });
    const bars = within(group).getAllByRole("button");
    expect(bars.filter((bar) => bar.tabIndex === 0)).toEqual([bars[6]]);
    bars[6]?.focus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(bars[5]).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(bars[0]).toHaveFocus();
    expect(bars[0]).toHaveAttribute("tabindex", "0");
  });

  it("is absent for a window without traces", async () => {
    const histogram = vi
      .spyOn(projectsApi, "traceHistogram")
      .mockResolvedValue({ bucket: "day", buckets: [] });
    renderApp(LIST);
    await waitFor(() => {
      expect(histogram).toHaveBeenCalled();
    });
    expect(screen.queryByRole("group", { name: "Traces over time" })).not.toBeInTheDocument();
  });

  it("hides behind a muted line when the request fails", async () => {
    vi.spyOn(projectsApi, "traceHistogram").mockRejectedValue(new ApiError(500, {}));
    renderApp(LIST);
    expect(await screen.findByText("Histogram unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Traces over time" })).not.toBeInTheDocument();
  });
});
