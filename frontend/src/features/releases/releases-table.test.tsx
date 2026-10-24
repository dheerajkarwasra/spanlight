import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  releasesApi,
  scoresApi,
  type Comparison,
  type ReleaseStats,
  type ScoreConfig,
  type ScoreDataType,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

// Each test renders the whole app; under load the first one's cold lazy route chunk took over 5 s.
vi.setConfig({ testTimeout: 20_000 });

function findTable() {
  return screen.findByRole("table", { name: "Releases" }, { timeout: 15_000 });
}

const PROJECT_ID = "proj_1";

function stats(release: string, overrides: Partial<ReleaseStats> = {}): ReleaseStats {
  return {
    release,
    first_seen_at: "2026-10-01T00:00:00Z",
    last_seen_at: "2026-10-02T00:00:00Z",
    traces: 120,
    llm_calls: 300,
    unpriced_calls: 0,
    error_rate: 0.05,
    p50_ms: 400,
    p95_ms: 1200,
    cost_usd: "1.25",
    input_tokens: 1000,
    output_tokens: 500,
    ...overrides,
  };
}

function setup(list: ReleaseStats[], path = `/${ORG_ID}/${PROJECT_ID}/releases?range=7d&env=prod`) {
  mockSignedIn([makeProject(PROJECT_ID, "Support")]);
  vi.spyOn(releasesApi, "list").mockResolvedValue(list);
  vi.spyOn(releasesApi, "failureCategories").mockRejectedValue(new ApiError(404, {}));
  // Never resolves: these tests look at the request and the table, not the comparison.
  const compare = vi
    .spyOn(releasesApi, "compare")
    .mockReturnValue(new Promise<Comparison>(() => undefined));
  renderApp(path);
  return compare;
}

afterEach(() => {
  window.localStorage.clear();
});

describe("Releases page defaults", () => {
  it("compares the previous release with the latest when the URL picks none", async () => {
    const compare = setup([stats("v3"), stats("v2"), stats("v1")]);

    await findTable();
    await vi.waitFor(() => {
      expect(compare).toHaveBeenCalledWith(
        PROJECT_ID,
        expect.objectContaining({ a: "v2", b: "v3", environment: "prod" }),
      );
    });
    expect(screen.getByRole("combobox", { name: "Baseline (A)" })).toHaveTextContent("v2");
    expect(screen.getByRole("combobox", { name: "Candidate (B)" })).toHaveTextContent("v3");
  });

  it("keeps the pair the URL picks", async () => {
    const compare = setup(
      [stats("v3"), stats("v2"), stats("v1")],
      `/${ORG_ID}/${PROJECT_ID}/releases?a=v1&b=v3`,
    );

    await findTable();
    await vi.waitFor(() => {
      expect(compare).toHaveBeenCalledWith(
        PROJECT_ID,
        expect.objectContaining({ a: "v1", b: "v3" }),
      );
    });
    expect(compare).not.toHaveBeenCalledWith(PROJECT_ID, expect.objectContaining({ a: "v2" }));
  });

  it("waits for the new window's list before comparing a derived pair, dimming the old rows", async () => {
    mockSignedIn([makeProject(PROJECT_ID, "Support")]);
    let resolveNext: (list: ReleaseStats[]) => void = () => undefined;
    vi.spyOn(releasesApi, "list")
      .mockResolvedValueOnce([stats("v3"), stats("v2"), stats("v1")])
      .mockReturnValueOnce(new Promise((resolve) => (resolveNext = resolve)));
    vi.spyOn(releasesApi, "failureCategories").mockRejectedValue(new ApiError(404, {}));
    const compare = vi
      .spyOn(releasesApi, "compare")
      .mockReturnValue(new Promise<Comparison>(() => undefined));
    const router = renderApp(`/${ORG_ID}/${PROJECT_ID}/releases?range=7d`);
    await findTable();
    await vi.waitFor(() => {
      expect(compare).toHaveBeenCalledTimes(1);
    });

    await router.navigate({
      to: ".",
      search: (previous: Record<string, unknown>) => ({ ...previous, range: "24h" }),
    } as never);

    // The old window's rows stay, dimmed; the old pair is not compared in the new window.
    await vi.waitFor(() => {
      expect(screen.getByRole("table", { name: "Releases" })).toHaveAttribute("aria-busy", "true");
    });
    // The phone tiles dim with the table.
    const tiles = screen.getByRole("list", { name: "Releases" });
    expect(tiles).toHaveAttribute("aria-busy", "true");
    expect(tiles).toHaveClass("opacity-60");
    expect(compare).toHaveBeenCalledTimes(1);

    resolveNext([stats("v5"), stats("v4")]);
    await vi.waitFor(() => {
      expect(compare).toHaveBeenLastCalledWith(
        PROJECT_ID,
        expect.objectContaining({ a: "v4", b: "v5" }),
      );
    });
    expect(compare).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("table", { name: "Releases" })).not.toHaveAttribute("aria-busy");
    expect(screen.getByRole("list", { name: "Releases" })).not.toHaveAttribute("aria-busy");
  });

  it("says so when the window has only one release", async () => {
    const compare = setup([stats("v1")]);

    await findTable();
    expect(await screen.findByText("Only one release in this window.")).toBeInTheDocument();
    expect(compare).not.toHaveBeenCalled();
  });
});

describe("Releases table", () => {
  it("lists each release with its stats and links to its traces, keeping range and env", async () => {
    setup([
      stats("v2"),
      stats("v1", { error_rate: null, p95_ms: null, cost_usd: null, llm_calls: 0 }),
    ]);

    const table = await findTable();
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent);
    expect(headers.slice(0, 7)).toEqual([
      "Release",
      "First seen",
      "Last seen",
      "Traces",
      "Error rate",
      "p95",
      "Cost",
    ]);
    expect(within(table).getByRole("columnheader", { name: "Release" })).toHaveClass("sticky");

    const link = within(table).getByRole("link", { name: "v2" });
    const url = new URL(link.getAttribute("href") ?? "", "http://localhost");
    // The app rewrites ids to slugs ("/acme/support/traces").
    expect(url.pathname).toMatch(/\/traces$/);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      range: "7d",
      env: "prod",
      release: "v2",
    });

    const row = within(table).getByRole("link", { name: "v2" }).closest("[role=row]");
    expect(row).toHaveTextContent("120");
    expect(row).toHaveTextContent("$1.25");
    // Unknown values are "—", never 0.
    const unknownRow = within(table).getByRole("link", { name: "v1" }).closest("[role=row]");
    expect(within(unknownRow as HTMLElement).getAllByText("—").length).toBeGreaterThanOrEqual(3);

    // The phone list stays in the page for widths below md.
    expect(screen.getAllByRole("link", { name: /v2/ })).toHaveLength(2);
  });
});

describe("Failure categories card", () => {
  function config(name: string, dataType: ScoreDataType): ScoreConfig {
    return {
      id: `cfg_${name}`,
      name,
      data_type: dataType,
      min_value: null,
      max_value: null,
      categories: [],
      description: null,
      implicit: false,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    };
  }

  it("skips the rates read when the score configs have no failure_category config", async () => {
    vi.spyOn(scoresApi, "configs").mockResolvedValue([config("helpfulness", "numeric")]);
    setup([stats("v2"), stats("v1")]);

    await findTable();
    expect(await screen.findByText("No failure categories yet")).toBeInTheDocument();
    // The read would only answer 404, which the browser logs on every visit.
    expect(vi.mocked(releasesApi.failureCategories)).not.toHaveBeenCalled();
  });

  it("reads the rates once the project has a categorical failure_category config", async () => {
    vi.spyOn(scoresApi, "configs").mockResolvedValue([config("failure_category", "categorical")]);
    setup([stats("v2"), stats("v1")]);

    await findTable();
    await vi.waitFor(() => {
      expect(vi.mocked(releasesApi.failureCategories)).toHaveBeenCalledTimes(1);
    });
  });
});
