import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  datasetsApi,
  insightsApi,
  projectsApi,
  type TraceDetail,
  type TraceListQuery,
  type TraceSummary,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

import { testSpan } from "./span-test-data";

const PROJECT_ID = "proj_1";
const TRACES_URL = `/${ORG_ID}/${PROJECT_ID}/traces`;

function summary(id: string): TraceSummary {
  return {
    trace_id: id,
    name: `trace-${id}`,
    environment: "production",
    release: null,
    external_user_id: null,
    session_id: null,
    tags: [],
    started_at: "2026-10-17T10:00:00Z",
    ended_at: "2026-10-17T10:00:01Z",
    duration_ms: 1000,
    span_count: 2,
    error_count: 0,
    input_tokens: 10,
    output_tokens: 5,
    cost_usd: "0.001",
    has_unpriced: false,
    models: [],
    error_message: null,
    error_class: null,
    has_scores: false,
    retry_calls: 0,
    retry_cost_usd: "0",
    scores: [],
    metadata: {},
    input_preview: null,
    output_preview: null,
  };
}

function detail(id: string): TraceDetail {
  return {
    ...summary(id),
    spans: [testSpan("root", null, 0, 1000), testSpan("child", "root", 10, 500)],
  };
}

/** The window is `width` px wide: `(min-width: 1280px)` matches from 1280, the phone query below 768. */
function mockWidth(width: number) {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches:
      (query === "(min-width: 1280px)" && width >= 1280) ||
      (query === "(max-width: 767px)" && width < 768),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

/** The list: t1, t2, t3; a search (`q`) finds only t3. */
function mockApis() {
  mockSignedIn([makeProject(PROJECT_ID, "Support")]);
  vi.spyOn(projectsApi, "overview").mockRejectedValue(new ApiError(500, {}));
  vi.spyOn(insightsApi, "list").mockReturnValue(new Promise(() => undefined));
  vi.spyOn(datasetsApi, "list").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(projectsApi, "traces").mockImplementation((_project, query: TraceListQuery) =>
    Promise.resolve({
      items: (query.q ? ["t3"] : ["t1", "t2", "t3"]).map(summary),
      next_cursor: null,
    }),
  );
  return vi
    .spyOn(projectsApi, "trace")
    .mockImplementation((_project, id) => Promise.resolve(detail(id)));
}

async function loadedTable() {
  await screen.findAllByText("trace-t1", {}, { timeout: 4000 });
  return screen.getByRole("table", { name: "Traces" });
}

/** The open peek, once its trace has loaded. */
async function peekSheet(name: string) {
  const sheet = await screen.findByRole("dialog", { name });
  await within(sheet).findByRole("heading", { level: 1, name });
  return sheet;
}

beforeEach(() => {
  mockApis();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("the peek sheet from 1280 px", () => {
  beforeEach(() => {
    mockWidth(1280);
  });

  it("opens the clicked row's trace in the sheet, with peek= in the URL", async () => {
    const router = renderApp(TRACES_URL);

    fireEvent.click(within(await loadedTable()).getAllByText("1.00 s")[1] as HTMLElement);

    const sheet = await peekSheet("trace-t2");
    expect(router.state.location.search).toMatchObject({ peek: "t2" });
    expect(router.state.location.pathname).toMatch(/\/traces$/);
    expect(within(sheet).getByText("2 of 3")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "trace-t2", hidden: true }).closest("[role=row]"),
    ).toHaveAttribute("data-state", "selected");

    // Opened here, so closing goes back to the list entry.
    fireEvent.click(within(sheet).getByRole("button", { name: "Close" }));
    await waitFor(() => {
      expect(router.state.location.search).not.toHaveProperty("peek");
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // The first render in a file loads the lazy route, which can take a while on a busy machine.
  }, 10_000);

  it("opens the peek for a plain click on the name link, not a modified or middle click", async () => {
    const router = renderApp(TRACES_URL);
    const link = within(await loadedTable()).getByRole("link", { name: "trace-t1" });

    fireEvent.click(link, { metaKey: true });
    fireEvent.click(link, { ctrlKey: true });
    fireEvent.click(link, { button: 1 });
    expect(router.state.location.search).not.toHaveProperty("peek");
    expect(link).toHaveAttribute("href", expect.stringMatching(/\/traces\/t1$/));

    fireEvent.click(link);
    await peekSheet("trace-t1");
    expect(router.state.location.search).toMatchObject({ peek: "t1" });
  });

  it("steps with j and k inside the sheet, never past the ends", async () => {
    const user = userEvent.setup();
    const router = renderApp(`${TRACES_URL}?peek=t1`);
    await peekSheet("trace-t1");

    await user.keyboard("k");
    expect(router.state.location.search).toMatchObject({ peek: "t1" });
    await user.keyboard("j");
    await peekSheet("trace-t2");
    await user.keyboard("j");
    const sheet = await peekSheet("trace-t3");
    expect(within(sheet).getByRole("button", { name: "Next trace" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await user.keyboard("j");
    expect(router.state.location.search).toMatchObject({ peek: "t3" });
    await user.keyboard("k");
    await peekSheet("trace-t2");
  });

  it("leaves j and k alone with the sheet closed", async () => {
    const router = renderApp(TRACES_URL);
    await loadedTable();

    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.body, { key: "k" });
    expect(router.state.location.search).not.toHaveProperty("peek");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes on Esc and drops peek from the URL", async () => {
    const user = userEvent.setup();
    const router = renderApp(`${TRACES_URL}?peek=t1`);
    await peekSheet("trace-t1");

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(router.state.location.search).not.toHaveProperty("peek");
    // Focus goes back to the peeked trace's row.
    expect(screen.getByRole("link", { name: "trace-t1" })).toHaveFocus();
  });

  it("carries the selected span and tab to the full page and to Copy link", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    renderApp(`${TRACES_URL}?peek=t1`);
    const sheet = await peekSheet("trace-t1");

    await user.click(within(sheet).getByRole("treeitem", { name: /child/ }));
    await user.click(within(sheet).getByRole("tab", { name: "Raw JSON" }));

    // The address bar shows the org and project slugs.
    const full = "/acme/support/traces/t1?span=child&tab=raw";
    expect(within(sheet).getByRole("link", { name: "Open full page" })).toHaveAttribute(
      "href",
      full,
    );
    await user.click(within(sheet).getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}${full}`);
  });

  it("moves focus to its title on open, back to the last peeked row on close", async () => {
    const user = userEvent.setup();
    renderApp(`${TRACES_URL}?peek=t1`);
    const sheet = await peekSheet("trace-t1");
    await waitFor(() => {
      expect(within(sheet).getByRole("heading", { level: 2, name: "trace-t1" })).toHaveFocus();
    });

    await user.keyboard("j");
    await peekSheet("trace-t2");
    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "trace-t2" })).toHaveFocus();
    });
  });

  it("keeps focus in the sheet when Next reaches the last row", async () => {
    const user = userEvent.setup();
    const router = renderApp(`${TRACES_URL}?peek=t2`);
    const sheet = await peekSheet("trace-t2");

    const next = within(sheet).getByRole("button", { name: "Next trace" });
    await user.click(next);
    await peekSheet("trace-t3");
    expect(next).toHaveFocus();
    expect(next).toHaveAttribute("aria-disabled", "true");

    // Still in the sheet, so the keys still work; Next at the end does nothing.
    await user.click(next);
    expect(router.state.location.search).toMatchObject({ peek: "t3" });
    await user.keyboard("k");
    await peekSheet("trace-t2");
  });

  it("ignores j and k from a dialog opened in the peek", async () => {
    const user = userEvent.setup();
    const router = renderApp(`${TRACES_URL}?peek=t1`);
    const sheet = await peekSheet("trace-t1");

    await user.click(within(sheet).getByRole("button", { name: "Add to dataset" }));
    const dialog = await screen.findByRole("dialog", { name: /Add to dataset/ });
    within(dialog).getByRole("button", { name: "Cancel" }).focus();
    await user.keyboard("j");

    expect(router.state.location.search).toMatchObject({ peek: "t1" });
    expect(screen.getByRole("dialog", { name: /Add to dataset/ })).toBeInTheDocument();
  });

  it("closes on browser Back after a row click opened it", async () => {
    const router = renderApp(TRACES_URL);
    fireEvent.click(within(await loadedTable()).getAllByText("1.00 s")[0] as HTMLElement);
    await peekSheet("trace-t1");

    router.history.back();

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(router.state.location.search).not.toHaveProperty("peek");
  });

  it("pads its own scroll box below the sticky summary bar, not the page", async () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(56);
    renderApp(`${TRACES_URL}?peek=t1`);
    const sheet = await peekSheet("trace-t1");

    const box = within(sheet).getByRole("tree").closest(".overflow-y-auto") as HTMLElement;
    await waitFor(() => {
      expect(box.style.scrollPaddingTop).toBe("64px");
    });
    expect(document.documentElement.style.scrollPaddingTop).toBe("");
  });

  it("keeps showing the peeked trace when the list refetches without it", async () => {
    const user = userEvent.setup();
    const router = renderApp(`${TRACES_URL}?peek=t1`);
    await peekSheet("trace-t1");

    await router.navigate({ to: ".", search: (prev) => ({ ...prev, q: "only-t3" }) });
    await waitFor(() => {
      expect(screen.queryByText("1 of 3")).not.toBeInTheDocument();
    });

    const sheet = await peekSheet("trace-t1");
    await user.keyboard("j");
    expect(router.state.location.search).toMatchObject({ peek: "t1" });
    expect(within(sheet).getByRole("button", { name: "Next trace" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });
});

describe("the traces list below 1280 px", () => {
  it("opens the full trace page on a row click", async () => {
    mockWidth(1024);
    const router = renderApp(TRACES_URL);

    fireEvent.click(within(await loadedTable()).getAllByText("1.00 s")[0] as HTMLElement);

    await waitFor(() => {
      expect(router.state.location.pathname).toMatch(/\/traces\/t1$/);
    });
    expect(router.state.location.search).not.toHaveProperty("peek");
    // Only the table renders: the phone's tiles are not built hidden beside it.
    expect(screen.queryByRole("list", { name: "Traces" })).not.toBeInTheDocument();
  });

  it("renders only the tiles on a phone", async () => {
    mockWidth(375);
    renderApp(TRACES_URL);

    const tiles = await screen.findByRole("list", { name: "Traces" }, { timeout: 4000 });
    expect(within(tiles).getByText("trace-t1")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Traces" })).not.toBeInTheDocument();
  });
});
