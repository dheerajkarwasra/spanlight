import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, projectsApi, type TraceSummary } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PROJECT_ID = "proj_1";
const TRACES_URL = `/${ORG_ID}/${PROJECT_ID}/traces`;
const NO_INPUT = "No input captured on this trace";

type TracePage = Awaited<ReturnType<typeof projectsApi.traces>>;

function trace(index: number, extra: Partial<TraceSummary> = {}): TraceSummary {
  return {
    trace_id: `t${index}`,
    name: `trace-${index}`,
    environment: "production",
    release: null,
    external_user_id: null,
    session_id: null,
    tags: [],
    started_at: "2026-10-17T10:00:00Z",
    ended_at: "2026-10-17T10:00:01Z",
    duration_ms: 1000,
    span_count: 1,
    error_count: 0,
    input_tokens: 10,
    output_tokens: 5,
    cost_usd: "0.001",
    has_unpriced: false,
    models: ["gpt-4.1"],
    error_message: null,
    error_class: null,
    has_scores: false,
    retry_calls: 0,
    retry_cost_usd: "0",
    scores: [],
    metadata: {},
    input_preview: null,
    output_preview: null,
    ...extra,
  };
}

function mockTraces(items: TraceSummary[]) {
  mockSignedIn([makeProject(PROJECT_ID, "Support")]);
  vi.spyOn(projectsApi, "overview").mockRejectedValue(new ApiError(500, {}));
  return vi.spyOn(projectsApi, "traces").mockResolvedValue({ items, next_cursor: null });
}

afterEach(() => {
  window.localStorage.clear();
});

/** The table once the rows are in (the loading table carries the same name). */
async function loadedTable() {
  // The first render in a file loads the lazy route, which can take a while on a busy machine.
  await screen.findAllByText("trace-1", {}, { timeout: 4000 });
  return screen.getByRole("table", { name: "Traces" });
}

describe("the traces table", () => {
  it("shows the input and output previews on one line, the full text in the title", async () => {
    const list = mockTraces([
      trace(1, { input_preview: "How do I reset my password?", output_preview: "Open Settings" }),
      trace(2),
    ]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    const input = within(table).getByText("How do I reset my password?");
    expect(input).toHaveAttribute("title", "How do I reset my password?");
    expect(input).toHaveClass("truncate");
    expect(within(table).getByText("Open Settings")).toHaveAttribute("title", "Open Settings");
    expect(
      within(table).getAllByLabelText("Unknown: No input captured on this trace"),
    ).toHaveLength(1);
    expect(list).toHaveBeenCalledWith(PROJECT_ID, expect.objectContaining({ preview: true }));
  });

  it("shows a placeholder, not 'not captured', while rows fetched without previews wait for them", async () => {
    window.localStorage.setItem("spanlight.columns.traces", "input:0,output:0");
    const list = mockTraces([]);
    let land: (page: TracePage) => void = () => undefined;
    list.mockImplementation((_projectId, query) =>
      query.preview
        ? new Promise<TracePage>((resolve) => {
            land = resolve;
          })
        : Promise.resolve({ items: [trace(1)], next_cursor: null }),
    );
    renderApp(TRACES_URL);

    const table = await loadedTable();
    await userEvent.click(screen.getByRole("button", { name: "Table settings" }));
    await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Input" }));
    await userEvent.keyboard("{Escape}");
    // The kept rows came from a request without previews: their null means "not asked".
    await within(table).findByRole("columnheader", { name: "Input" });
    expect(within(table).queryByLabelText(`Unknown: ${NO_INPUT}`)).toBeNull();
    expect(table.querySelector(".animate-shimmer")).not.toBeNull();

    act(() => {
      land({ items: [trace(1)], next_cursor: null });
    });
    expect(await within(table).findByLabelText(`Unknown: ${NO_INPUT}`)).toBeInTheDocument();
  });

  it("asks for previews only while the Input or Output column is shown", async () => {
    window.localStorage.setItem("spanlight.columns.traces", "input:0,output:0");
    const list = mockTraces([trace(1)]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    expect(within(table).queryByRole("columnheader", { name: "Input" })).not.toBeInTheDocument();
    expect(list).toHaveBeenCalled();
    for (const call of list.mock.calls) {
      expect(call[1].preview).toBeUndefined();
    }
  });

  it("leaves a hidden column out of the DOM", async () => {
    window.localStorage.setItem("spanlight.columns.traces", "model:0");
    mockTraces([trace(1)]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    expect(within(table).getByRole("columnheader", { name: "Cost" })).toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Model" })).not.toBeInTheDocument();
    expect(within(table).queryByText("gpt-4.1")).not.toBeInTheDocument();
  });

  it("shows a failed trace's error in the Output cell", async () => {
    mockTraces([
      trace(1, {
        error_count: 1,
        error_message: "429 Too Many Requests",
        output_preview: "partial answer",
      }),
    ]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    const row = within(table).getAllByRole("row")[1] as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    const headers = within(table).getAllByRole("columnheader");
    const output = cells[headers.findIndex((header) => header.textContent === "Output")];
    expect(output).toHaveTextContent("429 Too Many Requests");
    expect(output).not.toHaveTextContent("partial answer");
  });

  it("sorts by duration from its header: descending first, then ascending", async () => {
    const list = mockTraces([trace(1)]);
    const router = renderApp(TRACES_URL);

    const table = await loadedTable();
    fireEvent.click(within(table).getByRole("button", { name: "Sort by Duration" }));
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ sort: "duration" });
    });
    // Descending is the default order, so the URL and the request leave it out.
    expect(router.state.location.search).not.toHaveProperty("order");
    await waitFor(() => {
      expect(list).toHaveBeenLastCalledWith(
        PROJECT_ID,
        expect.objectContaining({ sort: "duration", order: undefined }),
      );
    });

    fireEvent.click(within(table).getByRole("button", { name: "Sort by Duration" }));
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ sort: "duration", order: "asc" });
    });
    expect(
      within(table).getByRole("columnheader", { name: "Duration" }).getAttribute("aria-sort"),
    ).toBe("ascending");
  });

  it("goes back to the default URL when Started is sorted newest first again", async () => {
    mockTraces([trace(1)]);
    const router = renderApp(`${TRACES_URL}?sort=started_at&order=asc`);

    const table = await loadedTable();
    fireEvent.click(within(table).getByRole("button", { name: "Sort by Started" }));
    await waitFor(() => {
      expect(router.state.location.search).not.toHaveProperty("order");
    });
    expect(router.state.location.search).not.toHaveProperty("sort");
  });

  it("offers Retry cost in Table settings while the list is not sorted by it", async () => {
    window.localStorage.setItem("spanlight.columns.traces", "retry_cost:1");
    mockTraces([trace(1)]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    expect(
      within(table).getByRole("columnheader", { name: "Retry cost" }).getAttribute("aria-sort"),
    ).toBe("none");
  });

  it("opens a trace when its row is clicked", async () => {
    mockTraces([trace(1)]);
    vi.spyOn(projectsApi, "trace").mockReturnValue(new Promise(() => undefined));
    const router = renderApp(TRACES_URL);

    const table = await loadedTable();
    fireEvent.click(within(table).getByText("1.00 s"));
    await waitFor(() => {
      // The address bar shows the org and project slugs.
      expect(router.state.location.pathname).toMatch(/\/traces\/t1$/);
    });
  });
});

describe("the phone tiles", () => {
  it("show the input preview under the name", async () => {
    mockTraces([trace(1, { input_preview: "Where is my order?" })]);
    mockPhone();
    renderApp(TRACES_URL);

    const tiles = await screen.findByRole("list", { name: "Traces" });
    expect(within(tiles).getByText("Where is my order?")).toBeInTheDocument();
  });

  it("show a placeholder, not 'not captured', while kept tiles wait for the new request", async () => {
    const list = mockTraces([]);
    let land: (page: TracePage) => void = () => undefined;
    list.mockImplementation((_projectId, query) =>
      query.q
        ? new Promise<TracePage>((resolve) => {
            land = resolve;
          })
        : Promise.resolve({ items: [trace(1)], next_cursor: null }),
    );
    mockPhone();
    const router = renderApp(TRACES_URL);

    const tiles = await screen.findByRole("list", { name: "Traces" });
    await within(tiles).findByText(`Unknown: ${NO_INPUT}`);
    await act(() => router.navigate({ to: ".", search: (prev) => ({ ...prev, q: "order" }) }));
    await waitFor(() => {
      expect(within(tiles).queryByText(`Unknown: ${NO_INPUT}`)).toBeNull();
    });
    expect(tiles.querySelector(".animate-shimmer")).not.toBeNull();

    act(() => {
      land({ items: [trace(1)], next_cursor: null });
    });
    expect(await within(tiles).findByText(`Unknown: ${NO_INPUT}`)).toBeInTheDocument();
  });
});

/** A phone: the page renders the tiles only below 768 px. */
function mockPhone() {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query === "(max-width: 767px)",
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

describe("the empty list", () => {
  it("says the range is empty once the project has traces", async () => {
    mockTraces([]);
    vi.spyOn(projectsApi, "onboarding").mockResolvedValue({
      has_traces: true,
      first_trace_at: "2026-10-01T00:00:00Z",
    });
    renderApp(TRACES_URL);

    expect(
      await screen.findByRole("heading", { name: "No traces in this range" }, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "See it in the live demo" }),
    ).not.toBeInTheDocument();
  });

  it("offers the key snippet and the live demo before the first trace arrives", async () => {
    mockTraces([]);
    vi.spyOn(projectsApi, "onboarding").mockResolvedValue({
      has_traces: false,
      first_trace_at: null,
    });
    vi.spyOn(projectsApi, "keys").mockResolvedValue([]);
    renderApp(TRACES_URL);

    expect(
      await screen.findByRole("button", { name: "See it in the live demo" }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Create an API key" })).toBeInTheDocument();
  });
});
