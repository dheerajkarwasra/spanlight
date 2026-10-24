import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { endUsersApi, projectsApi, type UserList, type UserStats } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

function list(overrides: Partial<UserList> = {}): UserList {
  return {
    items: [],
    next_cursor: null,
    window: { start: "2026-09-22T00:00:00Z", end: "2026-10-22T00:00:00Z" },
    computed_at: null,
    pending_traces: null,
    ...overrides,
  };
}

const ADA: UserStats = {
  external_user_id: "user-ada",
  traces: 12,
  llm_calls: 30,
  errors: 1,
  unpriced_calls: 0,
  cost_usd: "0.42",
  tokens: 9_000,
  first_seen_day: "2026-10-02",
  last_seen_day: "2026-10-21",
};

describe("UsersPage freshness", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("shows no freshness chip for an empty window (nothing computed, nothing pending)", async () => {
    vi.spyOn(endUsersApi, "list").mockResolvedValue(list());
    renderApp(`/${ORG_ID}/proj_1/users`);

    expect(await screen.findByText(/0 end users/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Not computed yet/ })).not.toBeInTheDocument();
  });

  it("says 'Not computed yet' when traces wait for the first pass", async () => {
    vi.spyOn(endUsersApi, "list").mockResolvedValue(list({ pending_traces: 12 }));
    renderApp(`/${ORG_ID}/proj_1/users`);

    expect(
      await screen.findByRole("button", {
        name: "Not computed yet · 12 traces in this window aren't included yet",
      }),
    ).toBeInTheDocument();
  });
});

describe("UsersPage search and sort", () => {
  beforeEach(() => {
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("sends the typed user ID as q after the debounce", async () => {
    const listSpy = vi.spyOn(endUsersApi, "list").mockResolvedValue(list({ items: [ADA] }));
    renderApp(`/${ORG_ID}/proj_1/users`);

    const box = await screen.findByRole("searchbox", { name: "Search by user ID" });
    await userEvent.type(box, "ad");
    // Not on every keystroke: nothing is sent until typing pauses.
    expect(listSpy).not.toHaveBeenCalledWith("proj_1", expect.objectContaining({ q: "ad" }));
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith("proj_1", expect.objectContaining({ q: "ad" }));
    });
    expect(listSpy).not.toHaveBeenCalledWith("proj_1", expect.objectContaining({ q: "a" }));
  });

  it("replaces the history entry for a search, sends Enter at once and clears on Escape", async () => {
    const listSpy = vi.spyOn(endUsersApi, "list").mockResolvedValue(list({ items: [ADA] }));
    const router = renderApp(`/${ORG_ID}/proj_1/users`);

    const box = await screen.findByRole("searchbox", { name: "Search by user ID" });
    const entries = window.history.length;
    await userEvent.type(box, "ad{Enter}");
    expect(listSpy).toHaveBeenLastCalledWith("proj_1", expect.objectContaining({ q: "ad" }));
    expect(window.history.length).toBe(entries);

    await userEvent.type(box, "{Escape}");
    expect(box).toHaveValue("");
    // The unsearched list is still cached, so the URL is what shows the search was cleared.
    await waitFor(() => {
      expect(new URLSearchParams(router.state.location.searchStr).has("q")).toBe(false);
    });
  });

  it("announces the first load", async () => {
    vi.spyOn(endUsersApi, "list").mockReturnValue(new Promise(() => undefined));
    renderApp(`/${ORG_ID}/proj_1/users`);

    const statuses = await screen.findAllByRole("status");
    expect(statuses.some((status) => status.textContent === "Loading users")).toBe(true);
  });

  it("keeps the cost cell under the table's sticky header and column", async () => {
    vi.spyOn(endUsersApi, "list").mockResolvedValue(list({ items: [ADA] }));
    renderApp(`/${ORG_ID}/proj_1/users`);

    const table = await screen.findByRole("table", { name: "End users" });
    await within(table).findByRole("link", { name: "user-ada" });
    // Only the tile raises the cost above its stretched link.
    for (const cell of within(table).getAllByRole("cell")) {
      expect(cell.querySelector(".z-10")).toBeNull();
    }
    expect(screen.getByRole("list", { name: "End users" }).querySelector(".z-10")).not.toBeNull();
  });

  it("flips a sort header between descending and ascending", async () => {
    const listSpy = vi.spyOn(endUsersApi, "list").mockResolvedValue(list({ items: [ADA] }));
    renderApp(`/${ORG_ID}/proj_1/users`);

    const table = await screen.findByRole("table", { name: "End users" });
    const cost = within(table).getByRole("columnheader", { name: "Cost" });
    expect(cost).toHaveAttribute("aria-sort", "descending");

    await userEvent.click(within(cost).getByRole("button"));
    await waitFor(() => {
      expect(cost).toHaveAttribute("aria-sort", "ascending");
    });
    expect(listSpy).toHaveBeenCalledWith(
      "proj_1",
      expect.objectContaining({ sort: "cost", order: "asc" }),
    );

    const traces = within(table).getByRole("columnheader", { name: "Traces" });
    await userEvent.click(within(traces).getByRole("button"));
    await waitFor(() => {
      expect(traces).toHaveAttribute("aria-sort", "descending");
    });
    expect(cost).toHaveAttribute("aria-sort", "none");
  });

  it("keeps the default sort out of the URL and still reads an explicit one", async () => {
    vi.spyOn(endUsersApi, "list").mockResolvedValue(list({ items: [ADA] }));
    const router = renderApp(`/${ORG_ID}/proj_1/users?sort=cost&order=desc`);

    const table = await screen.findByRole("table", { name: "End users" });
    const cost = within(table).getByRole("columnheader", { name: "Cost" });
    expect(cost).toHaveAttribute("aria-sort", "descending");
    await router.navigate({
      to: "/$orgId/$projectId/users",
      params: { orgId: ORG_ID, projectId: "proj_1" },
    });
    await waitFor(() => {
      expect(router.state.location.searchStr).toBe("");
    });

    await userEvent.click(within(cost).getByRole("button"));
    await waitFor(() => {
      expect(router.state.location.searchStr).toBe("?order=asc");
    });
  });

  it("keeps q, sort and order on the detail page's 'Users' breadcrumb", async () => {
    vi.spyOn(endUsersApi, "list").mockResolvedValue(list({ items: [ADA] }));
    vi.spyOn(endUsersApi, "get").mockResolvedValue({
      user: ADA,
      daily: [],
      recent_sessions: [],
      window: { start: "2026-09-22T00:00:00Z", end: "2026-10-22T00:00:00Z" },
      computed_at: null,
    });
    renderApp(`/${ORG_ID}/proj_1/users?sort=errors&order=asc&q=ad&range=7d`);

    const table = await screen.findByRole("table", { name: "End users" });
    await userEvent.click(await within(table).findByRole("link", { name: "user-ada" }));
    const crumb = await screen.findByRole("link", { name: "Users" });
    const href = new URL(crumb.getAttribute("href") ?? "", "http://localhost");
    expect(href.pathname).toMatch(/\/users$/);
    expect(href.searchParams.get("q")).toBe("ad");
    expect(href.searchParams.get("sort")).toBe("errors");
    expect(href.searchParams.get("order")).toBe("asc");
    expect(href.searchParams.get("range")).toBe("7d");
  });

  it("offers the key snippet and the live demo before any trace arrived", async () => {
    vi.spyOn(endUsersApi, "list").mockResolvedValue(list());
    vi.spyOn(projectsApi, "onboarding").mockResolvedValue({
      has_traces: false,
      first_trace_at: null,
    });
    vi.spyOn(projectsApi, "keys").mockResolvedValue([]);
    renderApp(`/${ORG_ID}/proj_1/users`);

    expect(await screen.findByRole("heading", { name: "No end users yet" })).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "See it in the live demo" }),
    ).toBeInTheDocument();
  });
});
