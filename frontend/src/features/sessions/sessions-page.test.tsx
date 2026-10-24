import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { projectsApi, type SessionListItem, type SessionPage } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

function page(overrides: Partial<SessionPage> = {}): SessionPage {
  return { items: [], next_cursor: null, cost_p99_usd: null, ...overrides };
}

function session(overrides: Partial<SessionListItem> = {}): SessionListItem {
  return {
    session_id: "chat_1",
    trace_count: 4,
    first_at: "2026-10-21T09:00:00Z",
    last_at: "2026-10-21T09:30:00Z",
    cost_usd: "0.0100",
    has_unpriced: false,
    error_count: 0,
    external_user_id: "user-ada",
    user_count: 1,
    input_tokens: 1200,
    output_tokens: 300,
    duration_ms: 1_800_000,
    last_trace_id: "trace-last",
    last_input_preview: "Where is my order?",
    ...overrides,
  };
}

const FLAG = "Above the 99th percentile of session cost in this window";
const NO_INPUT = "Unknown: No input captured on the last turn";

describe("SessionsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
  });

  it("lists user, turns, tokens, the cost flag above p99 and the last turn's input", async () => {
    const listSpy = vi.spyOn(projectsApi, "sessions").mockResolvedValue(
      page({
        cost_p99_usd: "0.0500",
        items: [
          session(),
          session({
            session_id: "chat_2",
            cost_usd: "0.9000",
            external_user_id: null,
            user_count: 3,
            last_trace_id: null,
            last_input_preview: null,
          }),
        ],
      }),
    );
    renderApp(`/${ORG_ID}/proj_1/sessions`);

    const table = await screen.findByRole("table", { name: "Sessions" });
    await within(table).findByRole("link", { name: "chat_2" });
    const [first, second] = within(table).getAllByRole("row").slice(1);
    if (!first || !second) throw new Error("expected two rows");
    expect(within(first).getByText("user-ada")).toBeInTheDocument();
    expect(within(first).getByText("4")).toBeInTheDocument();
    expect(within(first).getByText("1,500")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Where is my order?" })).toHaveAttribute(
      "href",
      expect.stringContaining("/traces/trace-last"),
    );
    expect(within(first).queryByLabelText(FLAG)).not.toBeInTheDocument();
    expect(within(second).getByLabelText(FLAG)).toBeInTheDocument();
    expect(within(second).getByText("3 users")).toBeInTheDocument();
    // Previews are asked for while the Last turn column shows.
    expect(listSpy).toHaveBeenCalledWith(
      "proj_1",
      expect.objectContaining({ sort: "last", order: "desc", preview: true }),
    );
  });

  it("shows a placeholder, not 'not captured', while rows fetched without previews wait for them", async () => {
    window.localStorage.setItem("spanlight.columns.sessions", "last_turn:0");
    let land: (page: SessionPage) => void = () => undefined;
    const rows = page({ items: [session({ last_input_preview: null })] });
    vi.spyOn(projectsApi, "sessions").mockImplementation((_projectId, query) =>
      query.preview
        ? new Promise<SessionPage>((resolve) => {
            land = resolve;
          })
        : Promise.resolve(rows),
    );
    renderApp(`/${ORG_ID}/proj_1/sessions`);

    const table = await screen.findByRole("table", { name: "Sessions" });
    await within(table).findByRole("link", { name: "chat_1" });
    await userEvent.click(screen.getByRole("button", { name: "Table settings" }));
    await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Last turn" }));
    await userEvent.keyboard("{Escape}");
    // The kept rows came from a request without previews: their null means "not asked".
    await within(table).findByRole("columnheader", { name: "Last turn" });
    expect(within(table).queryByLabelText(NO_INPUT)).toBeNull();
    expect(table.querySelector(".animate-shimmer")).not.toBeNull();

    act(() => {
      land(rows);
    });
    expect(await within(table).findByLabelText(NO_INPUT)).toBeInTheDocument();
  });

  it("sends the typed session or user ID as q after the debounce", async () => {
    const listSpy = vi
      .spyOn(projectsApi, "sessions")
      .mockResolvedValue(page({ items: [session()] }));
    renderApp(`/${ORG_ID}/proj_1/sessions`);

    const box = await screen.findByRole("searchbox", { name: "Search by session ID or user ID" });
    await userEvent.type(box, "chat");
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith("proj_1", expect.objectContaining({ q: "chat" }));
    });
    expect(listSpy).not.toHaveBeenCalledWith("proj_1", expect.objectContaining({ q: "c" }));
  });

  it("flips the Cost header between descending and ascending", async () => {
    const listSpy = vi
      .spyOn(projectsApi, "sessions")
      .mockResolvedValue(page({ items: [session()] }));
    renderApp(`/${ORG_ID}/proj_1/sessions`);

    const table = await screen.findByRole("table", { name: "Sessions" });
    const cost = within(table).getByRole("columnheader", { name: "Cost" });
    await userEvent.click(within(cost).getByRole("button"));
    await waitFor(() => {
      expect(cost).toHaveAttribute("aria-sort", "descending");
    });
    await userEvent.click(within(cost).getByRole("button"));
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        "proj_1",
        expect.objectContaining({ sort: "cost", order: "asc" }),
      );
    });
  });

  it("keeps the default sort out of the URL and still reads an explicit one", async () => {
    vi.spyOn(projectsApi, "sessions").mockResolvedValue(page({ items: [session()] }));
    const router = renderApp(`/${ORG_ID}/proj_1/sessions?sort=last&order=desc`);

    const table = await screen.findByRole("table", { name: "Sessions" });
    expect(within(table).getByRole("columnheader", { name: "Last turn" })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    await router.navigate({
      to: "/$orgId/$projectId/sessions",
      params: { orgId: ORG_ID, projectId: "proj_1" },
    });
    await waitFor(() => {
      expect(router.state.location.searchStr).toBe("");
    });

    const cost = within(table).getByRole("columnheader", { name: "Cost" });
    await userEvent.click(within(cost).getByRole("button"));
    await waitFor(() => {
      expect(router.state.location.searchStr).toBe("?sort=cost");
    });
  });

  it("says when a search matched nothing", async () => {
    vi.spyOn(projectsApi, "sessions").mockResolvedValue(page());
    renderApp(`/${ORG_ID}/proj_1/sessions?q=nobody`);

    expect(
      await screen.findByRole("heading", { name: "No matching sessions" }),
    ).toBeInTheDocument();
  });

  it("offers the key snippet and the live demo before any trace arrived", async () => {
    vi.spyOn(projectsApi, "sessions").mockResolvedValue(page());
    vi.spyOn(projectsApi, "onboarding").mockResolvedValue({
      has_traces: false,
      first_trace_at: null,
    });
    vi.spyOn(projectsApi, "keys").mockResolvedValue([]);
    renderApp(`/${ORG_ID}/proj_1/sessions`);

    expect(await screen.findByRole("heading", { name: "No sessions yet" })).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "See it in the live demo" }),
    ).toBeInTheDocument();
  });

  it("replaces the history entry for a search, sends Enter at once and clears on Escape", async () => {
    const listSpy = vi
      .spyOn(projectsApi, "sessions")
      .mockResolvedValue(page({ items: [session()] }));
    const router = renderApp(`/${ORG_ID}/proj_1/sessions`);

    const box = await screen.findByRole("searchbox", { name: "Search by session ID or user ID" });
    const entries = window.history.length;
    await userEvent.type(box, "ch{Enter}");
    await waitFor(() => {
      expect(listSpy).toHaveBeenLastCalledWith("proj_1", expect.objectContaining({ q: "ch" }));
    });
    expect(window.history.length).toBe(entries);

    await userEvent.type(box, "{Escape}");
    expect(box).toHaveValue("");
    await waitFor(() => {
      expect(new URLSearchParams(router.state.location.searchStr).has("q")).toBe(false);
    });
  });

  it("flags a later page's row against the first page's 99th percentile", async () => {
    vi.spyOn(projectsApi, "sessions").mockImplementation((_projectId, query) =>
      Promise.resolve(
        query.cursor
          ? page({ cost_p99_usd: null, items: [session({ session_id: "chat_9", cost_usd: "2" })] })
          : page({ cost_p99_usd: "0.05", next_cursor: "c1", items: [session()] }),
      ),
    );
    renderApp(`/${ORG_ID}/proj_1/sessions`);

    const table = await screen.findByRole("table", { name: "Sessions" });
    await within(table).findByRole("link", { name: "chat_1" });
    const [loadMore] = screen.getAllByRole("button", { name: "Load more" });
    if (!loadMore) throw new Error("expected Load more");
    await userEvent.click(loadMore);
    await within(table).findByRole("link", { name: "chat_9" });
    const rows = within(table).getAllByRole("row");
    const later = rows.find((row) => within(row).queryByText("chat_9"));
    if (!later) throw new Error("expected the page-2 row");
    expect(within(later).getByLabelText(FLAG)).toBeInTheDocument();
  });

  it("asks for no previews on a phone, where the tiles show no last turn", async () => {
    const listSpy = vi
      .spyOn(projectsApi, "sessions")
      .mockResolvedValue(page({ items: [session()] }));
    const media = vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query === "(max-width: 767px)",
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
    try {
      renderApp(`/${ORG_ID}/proj_1/sessions`);
      await screen.findByRole("list", { name: "Sessions" });
      expect(listSpy).toHaveBeenCalled();
      for (const [, query] of listSpy.mock.calls) {
        expect(query.preview).toBeUndefined();
      }
    } finally {
      media.mockRestore();
    }
  });
});
