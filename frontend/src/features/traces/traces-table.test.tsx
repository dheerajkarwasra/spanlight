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
    root_received: true,
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
    expect(list).toHaveBeenCalledWith(
      PROJECT_ID,
      expect.objectContaining({ preview: true }),
      expect.any(AbortSignal),
    );
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

  it("keeps errors first and never calls a trace without a known root OK", async () => {
    mockTraces([
      trace(1),
      trace(2, { root_received: false }),
      trace(3, { root_received: null }),
      trace(4, { root_received: false, error_count: 2 }),
    ]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    const headers = within(table).getAllByRole("columnheader");
    const durationAt = headers.findIndex((header) => header.textContent === "Sort by Duration");
    const [ok, noRoot, unknownRoot, failed] = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((row) => ({ row, duration: within(row).getAllByRole("cell")[durationAt] }));
    if (!ok || !noRoot || !unknownRoot || !failed) {
      throw new Error("expected four rows");
    }

    expect(within(ok.row).getByRole("img", { name: "OK" })).toBeInTheDocument();
    expect(ok.row).not.toHaveTextContent(/Root/);
    expect(ok.duration).not.toHaveTextContent("so far");

    expect(within(noRoot.row).queryByRole("img", { name: "OK" })).toBeNull();
    expect(within(noRoot.row).getByText("Root span not received")).toBeInTheDocument();
    expect(noRoot.duration).toHaveTextContent(/so far$/);

    expect(within(unknownRoot.row).queryByRole("img", { name: "OK" })).toBeNull();
    expect(within(unknownRoot.row).getByText("Root status unavailable")).toBeInTheDocument();
    expect(unknownRoot.duration).not.toHaveTextContent("so far");

    expect(within(failed.row).getByRole("img", { name: "Failed, 2 errors" })).toBeInTheDocument();
    expect(within(failed.row).getByText("Root span not received")).toBeInTheDocument();
  });

  it("sizes two-line name and duration cells to fit a compact 32 px row", async () => {
    mockTraces([trace(1, { root_received: false })]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    const row = within(table).getAllByRole("row")[1] as HTMLElement;
    // Two caption lines at the 12 px token's 1.4 line height (16.8 px each) overflow the
    // 31 px a compact row leaves inside its divider; each line must stay at 15 px.
    const captions = [...row.querySelectorAll<HTMLElement>("[class*='text-xs']")];
    expect(captions.length).toBeGreaterThan(0);
    for (const caption of captions) {
      expect(caption.className).toContain("text-xs/[15px]");
    }
  });

  it("gives the 13 px name a 16 px line in comfortable rows so descenders are not clipped", async () => {
    mockTraces([trace(1, { root_received: false })]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    const row = within(table).getAllByRole("row")[1] as HTMLElement;
    // Comfortable rows leave 39 px: a 16 px name line and the 15 px caption fit; compact keeps 15 px.
    expect(row).toHaveAttribute("data-density", "comfortable");
    const link = within(row).getByRole("link");
    const lines = link.closest<HTMLElement>(".flex-col");
    expect(lines?.className).toContain("leading-[15px]");
    expect(lines?.className).toContain("group-data-[density=comfortable]/row:leading-4");
  });

  it("shows an unknown duration as plain —, never — so far", async () => {
    mockTraces([trace(1, { root_received: false, duration_ms: Number.NaN })]);
    renderApp(TRACES_URL);

    const table = await loadedTable();
    const headers = within(table).getAllByRole("columnheader");
    const durationAt = headers.findIndex((header) => header.textContent === "Sort by Duration");
    const cell = within(within(table).getAllByRole("row")[1] as HTMLElement).getAllByRole("cell")[
      durationAt
    ];
    expect(cell).toHaveTextContent(/^—$/);
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
        expect.any(AbortSignal),
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

  it("name a missing or unknown root instead of calling the trace OK", async () => {
    mockTraces([trace(1, { root_received: false }), trace(2, { root_received: null })]);
    mockPhone();
    renderApp(TRACES_URL);

    const tiles = await screen.findByRole("list", { name: "Traces" });
    const [noRoot, unknownRoot] = within(tiles).getAllByRole("listitem");
    expect(within(tiles).queryByRole("img", { name: "OK" })).toBeNull();
    expect(noRoot).toHaveTextContent("Root span not received");
    expect(noRoot).toHaveTextContent("so far");
    expect(unknownRoot).toHaveTextContent("Root status unavailable");
    expect(unknownRoot).not.toHaveTextContent("so far");
  });

  it("show an unknown duration as plain —, never — so far", async () => {
    mockTraces([trace(1, { root_received: false, duration_ms: Number.NaN })]);
    mockPhone();
    renderApp(TRACES_URL);

    const tiles = await screen.findByRole("list", { name: "Traces" });
    expect(tiles).toHaveTextContent("—");
    expect(tiles).not.toHaveTextContent("so far");
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
