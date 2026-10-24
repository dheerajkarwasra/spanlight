import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { visibleColumns } from "@/components/data-table";
import { ApiError, authApi, insightsApi, type Insight } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

import { findingColumns } from "./findings-columns";

const PATH = `/${ORG_ID}/proj_1/doctor`;

function finding(id: string, title: string): Insight {
  return {
    id,
    kind: "error_spike",
    label: "Error spike",
    severity: "critical",
    status: "open",
    title,
    summary: "",
    failure_layer: "provider",
    certainty: "measured",
    evidence: { trace_ids: [], metrics: {}, window: { start: "", end: "" } },
    suggested_fix: "",
    verification: "",
    first_seen_at: "2026-10-21T09:42:00Z",
    last_seen_at: "2026-10-21T14:02:00Z",
    occurrences: 6,
    resolved_at: null,
    muted_until: null,
    mute_reason: null,
    acknowledged_at: null,
  };
}

const ITEMS = [finding("ins_1", "Error rate spiked"), finding("ins_2", "Latency up")];

async function loadedTable() {
  await screen.findAllByText("Error rate spiked", {}, { timeout: 10_000 });
  return screen.getByRole("table", { name: "Insights" });
}

// The first test loads the lazy route, which can take a while on a busy machine.
describe("the Doctor findings table", { timeout: 15_000 }, () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(insightsApi, "list").mockResolvedValue({ items: ITEMS, next_cursor: null });
    vi.spyOn(insightsApi, "summary").mockResolvedValue({
      open_critical: 2,
      open_warning: 0,
      open_info: 0,
    });
    vi.spyOn(insightsApi, "detectorRuns").mockResolvedValue([]);
  });

  it("shows the columns, Occurrences explained, and links each title", async () => {
    renderApp(PATH);
    const table = await loadedTable();
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent.trim());
    expect(headers.slice(1, 7)).toEqual([
      "Finding",
      "Kind",
      "Layer",
      "Occurrences",
      "Last seen",
      "Status",
    ]);
    const link = within(table).getByRole("link", { name: "Error rate spiked" });
    expect(link).toHaveAttribute("href", expect.stringMatching(/\/doctor\/ins_1$/));
    await userEvent.hover(within(table).getByRole("button", { name: "Occurrences" }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Detector runs that saw this problem",
    );
  });

  it("gives a member no checkboxes and no bulk bar", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      memberships: ME.memberships.map((m) => ({ ...m, role: "member" as const })),
    });
    renderApp(PATH);
    const table = await loadedTable();
    expect(within(table).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Select" })).not.toBeInTheDocument();
  });

  it("acknowledges each ticked finding, keeps the failed ones, and retries only those", async () => {
    const items = [...ITEMS, finding("ins_3", "Cost up")];
    let failing = true;
    let lists = 0;
    // The refetch after the run lists the rows in another order.
    vi.spyOn(insightsApi, "list").mockImplementation(() => {
      lists += 1;
      return Promise.resolve({
        items: lists === 1 ? items : [...items].reverse(),
        next_cursor: null,
      });
    });
    const acknowledge = vi
      .spyOn(insightsApi, "acknowledge")
      .mockImplementation((_project, id) =>
        id !== "ins_1" && failing
          ? Promise.reject(new ApiError(409, { code: "INVALID_TRANSITION" }))
          : Promise.resolve(items[0] as Insight),
      );
    renderApp(PATH);
    const table = await loadedTable();
    await userEvent.click(
      within(table).getByRole("checkbox", { name: "Select every insight shown" }),
    );
    const bar = screen.getByRole("region", { name: "Selected insights" });
    expect(bar).toHaveTextContent("3 selected");
    // Rows scrolled into view by the page land clear of the floating bar (WCAG 2.4.11).
    const wrappers = [...document.querySelectorAll("div")].filter((div) => div.contains(table));
    expect(wrappers.some((div) => div.classList.contains("[&_[role=row]]:scroll-mb-24"))).toBe(
      true,
    );
    await userEvent.click(within(bar).getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => {
      expect(bar.querySelector("[aria-live]")).toHaveTextContent(
        "2 selected · 1 acknowledged · 2 failed",
      );
    });
    expect(acknowledge.mock.calls.map((call) => call[1])).toEqual(["ins_1", "ins_2", "ins_3"]);
    expect(within(table).getByRole("checkbox", { name: "Select Latency up" })).toBeChecked();
    expect(within(table).getByRole("checkbox", { name: "Select Cost up" })).toBeChecked();
    expect(
      within(table).getByRole("checkbox", { name: "Select Error rate spiked" }),
    ).not.toBeChecked();
    await waitFor(() => {
      // The refetch put "Cost up" first; the result line still belongs to the selection.
      expect(within(table).getAllByRole("link")[0]).toHaveTextContent("Cost up");
    });
    expect(bar.querySelector("[aria-live]")).toHaveTextContent("1 acknowledged · 2 failed");

    failing = false;
    acknowledge.mockClear();
    await userEvent.click(within(bar).getByRole("button", { name: "retry" }));
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Selected insights" })).not.toBeInTheDocument();
    });
    expect(acknowledge.mock.calls.map((call) => call[1])).toEqual(["ins_2", "ins_3"]);
    expect(table).toHaveFocus();
  });

  it("keeps the retry button focused, outside the live line, while a retry fails again", async () => {
    // "Latency up" fails every time, once `release` is called.
    let pending: (() => void)[] = [];
    const release = () => {
      pending.forEach((fail) => {
        fail();
      });
      pending = [];
    };
    vi.spyOn(insightsApi, "acknowledge").mockImplementation((_project, id) =>
      id === "ins_1"
        ? Promise.resolve(ITEMS[0] as Insight)
        : new Promise((_resolve, reject) => {
            pending.push(() => {
              reject(new ApiError(409, { code: "INVALID_TRANSITION" }));
            });
          }),
    );
    renderApp(PATH);
    const table = await loadedTable();
    await userEvent.click(
      within(table).getByRole("checkbox", { name: "Select every insight shown" }),
    );
    const bar = screen.getByRole("region", { name: "Selected insights" });
    await userEvent.click(within(bar).getByRole("button", { name: "Acknowledge" }));
    release();
    const retry = await within(bar).findByRole("button", { name: "retry" });
    expect(retry.closest("[aria-live]")).toBeNull();
    expect(bar.querySelector("[aria-live]")).toHaveTextContent("1 selected · 1 acknowledged");

    await userEvent.click(retry);
    // While the retry runs, the line and its button stay put and the button holds focus.
    expect(within(bar).getByRole("button", { name: "retry" })).toBe(retry);
    expect(retry).toHaveAttribute("aria-disabled", "true");
    expect(retry).toHaveFocus();
    release();
    await waitFor(() => {
      expect(bar.querySelector("[aria-live]")).toHaveTextContent("0 acknowledged · 1 failed");
    });
    expect(retry).toHaveFocus();
  });

  it("forgets the failure line once a retry succeeds, so re-ticking shows no stale retry", async () => {
    let failing = true;
    vi.spyOn(insightsApi, "acknowledge").mockImplementation((_project, id) =>
      id === "ins_2" && failing
        ? Promise.reject(new ApiError(409, { code: "INVALID_TRANSITION" }))
        : Promise.resolve(ITEMS[0] as Insight),
    );
    renderApp(PATH);
    const table = await loadedTable();
    const selectAll = within(table).getByRole("checkbox", { name: "Select every insight shown" });
    await userEvent.click(selectAll);
    const bar = screen.getByRole("region", { name: "Selected insights" });
    await userEvent.click(within(bar).getByRole("button", { name: "Acknowledge" }));
    const retry = await within(bar).findByRole("button", { name: "retry" });
    failing = false;
    await userEvent.click(retry);
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Selected insights" })).not.toBeInTheDocument();
    });

    await userEvent.click(within(table).getByRole("checkbox", { name: "Select Latency up" }));
    const again = screen.getByRole("region", { name: "Selected insights" });
    expect(again.querySelector("[aria-live]")).toHaveTextContent(/^1 selected$/);
    expect(within(again).queryByRole("button", { name: "retry" })).toBeNull();
  });

  it("hides Layer and Occurrences by default when the table box is narrower than every track", () => {
    const columns = findingColumns({
      orgId: ORG_ID,
      projectId: "proj_1",
      selection: {
        isSelected: () => false,
        toggle: () => undefined,
        state: "none",
        toggleAll: () => undefined,
      },
    });
    const ids = (width: number) => visibleColumns(columns, {}, width).map((col) => col.id);
    expect(ids(787)).toEqual(["select", "finding", "kind", "last_seen", "status"]);
    expect(ids(1019)).toContain("layer");
    expect(ids(1019)).toContain("occurrences");
    // A viewer's choice in Table settings still wins.
    expect(visibleColumns(columns, { layer: true }, 787).map((col) => col.id)).toContain("layer");
  });

  it("mutes every ticked finding with one dialog", async () => {
    const mute = vi
      .spyOn(insightsApi, "mute")
      .mockImplementation((_project, id) =>
        Promise.resolve(ITEMS.find((item) => item.id === id) as Insight),
      );
    renderApp(PATH);
    const table = await loadedTable();
    await userEvent.click(
      within(table).getByRole("checkbox", { name: "Select Error rate spiked" }),
    );
    await userEvent.click(within(table).getByRole("checkbox", { name: "Select Latency up" }));
    const bar = screen.getByRole("region", { name: "Selected insights" });
    await userEvent.click(within(bar).getByRole("button", { name: "Mute…" }));

    const dialog = await screen.findByRole("dialog", { name: "Mute 2 insights" });
    await userEvent.click(within(dialog).getByRole("button", { name: "7 days" }));
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Known backfill");
    await userEvent.click(within(dialog).getByRole("button", { name: "Mute insights" }));

    await waitFor(() => {
      expect(mute).toHaveBeenCalledTimes(2);
    });
    const [first, second] = mute.mock.calls;
    expect(first?.[2]).toEqual(second?.[2]);
    expect(first?.[2].reason).toBe("Known backfill");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Selected insights" })).not.toBeInTheDocument();
    });
    // The bar (and its Mute… button) left, so the closing dialog hands focus to the table.
    await waitFor(() => {
      expect(table).toHaveFocus();
    });
  });
});
