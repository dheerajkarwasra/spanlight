import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, projectsApi, type FilterOptions, type TraceSummary } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PROJECT_ID = "proj_1";
const LIST = `/${ORG_ID}/${PROJECT_ID}/traces`;

const FILTERS: FilterOptions = {
  environments: [],
  releases: [],
  models: [],
  tags: [],
  score_names: [],
  metadata_keys: ["customer_id", "region"],
  trace_names: [],
};

function trace(id: string, metadata: Record<string, string>): TraceSummary {
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
    span_count: 1,
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
    metadata,
  };
}

function mockProject(promoted: string[]) {
  mockSignedIn([{ ...makeProject(PROJECT_ID, "Support"), promoted_metadata_keys: promoted }]);
  vi.spyOn(projectsApi, "filters").mockResolvedValue(FILTERS);
  vi.spyOn(projectsApi, "overview").mockRejectedValue(new ApiError(500, {}));
}

function searchOf(link: HTMLElement) {
  return new URL(link.getAttribute("href") ?? "", "http://localhost").searchParams;
}

async function tracesTable() {
  return within(await screen.findByRole("table", { name: "Traces" }));
}

describe("the trace list's metadata chips", () => {
  beforeEach(() => {
    mockProject(["customer_id", "feature"]);
  });

  it("shows a chip only for the promoted keys a trace carries", async () => {
    vi.spyOn(projectsApi, "traces").mockResolvedValue({
      items: [
        trace("t1", { customer_id: "acme", region: "eu-west-1" }),
        trace("t2", { region: "us-east-1" }),
      ],
      next_cursor: null,
    });
    renderApp(LIST);

    const table = await tracesTable();
    expect(table.getByRole("columnheader", { name: "Metadata" })).toBeInTheDocument();
    const [, first, second] = table.getAllByRole("row");
    if (!first || !second) {
      throw new Error("expected two trace rows");
    }
    expect(within(first).getByRole("link", { name: "customer_id acme" })).toBeInTheDocument();
    expect(within(first).queryByText("eu-west-1")).not.toBeInTheDocument();
    expect(within(second).queryByText("us-east-1")).not.toBeInTheDocument();
  });

  it("links a chip to the list filtered by its pair", async () => {
    vi.spyOn(projectsApi, "traces").mockResolvedValue({
      items: [trace("t1", { customer_id: "acme" })],
      next_cursor: null,
    });
    renderApp(LIST);

    const link = (await tracesTable()).getByRole("link", { name: "customer_id acme" });
    expect(searchOf(link).get("metadata_key")).toBe("customer_id");
    expect(searchOf(link).get("metadata_value")).toBe("acme");
  });

  it("folds the chips past the first into a count", async () => {
    vi.spyOn(projectsApi, "traces").mockResolvedValue({
      items: [trace("t1", { customer_id: "acme", feature: "triage" })],
      next_cursor: null,
    });
    renderApp(LIST);

    const table = await tracesTable();
    expect(table.getByText("+1")).toBeInTheDocument();
    expect(table.getByText(/1 more: feature triage/)).toBeInTheDocument();
  });

  it("has no metadata column when the project promotes no key", async () => {
    mockProject([]);
    vi.spyOn(projectsApi, "traces").mockResolvedValue({
      items: [trace("t1", { customer_id: "acme" })],
      next_cursor: null,
    });
    renderApp(LIST);

    const table = await tracesTable();
    expect(table.queryByRole("columnheader", { name: "Metadata" })).not.toBeInTheDocument();
    expect(table.queryByText("acme")).not.toBeInTheDocument();
  });

  it("sends the metadata filter from the URL to the list endpoint", async () => {
    const traces = vi
      .spyOn(projectsApi, "traces")
      .mockResolvedValue({ items: [], next_cursor: null });
    renderApp(`${LIST}?metadata_key=customer_id&metadata_value=acme&name=summarize`);

    expect(await screen.findByText("customer_id")).toBeInTheDocument();
    expect(traces).toHaveBeenCalledWith(
      PROJECT_ID,
      expect.objectContaining({
        metadata_key: "customer_id",
        metadata_value: "acme",
        name: "summarize",
      }),
    );
  });
});

describe("the trace detail's metadata block", () => {
  beforeEach(() => {
    mockProject(["customer_id"]);
  });

  function mockTrace(metadata: Record<string, string>) {
    vi.spyOn(projectsApi, "trace").mockResolvedValue({ ...trace("t1", metadata), spans: [] });
  }

  it("lists every pair by key and links each value to the filtered list", async () => {
    mockTrace({ region: "eu-west-1", customer_id: "acme", "a.b-c": "x" });
    renderApp(`${LIST}/t1`);

    const block = within(await screen.findByRole("region", { name: "Metadata" }));
    expect(block.getByText("3 keys")).toBeInTheDocument();
    const links = block.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["x", "acme", "eu-west-1"]);
    const dotted = block.getByRole("link", { name: "Show traces where a.b-c = x" });
    expect(searchOf(dotted).get("metadata_key")).toBe("a.b-c");
    expect(searchOf(dotted).get("metadata_value")).toBe("x");
  });

  it("shows a redacted value as the badge and filters on the stored value", async () => {
    mockTrace({ support_note: "[REDACTED]" });
    renderApp(`${LIST}/t1`);

    const block = within(await screen.findByRole("region", { name: "Metadata" }));
    const link = block.getByRole("link", { name: "Show traces where support_note = [REDACTED]" });
    expect(within(link).getByText("redacted")).toHaveAttribute("title", "[REDACTED]");
    expect(searchOf(link).get("metadata_value")).toBe("[REDACTED]");
  });

  it("says how to set metadata when the trace has none", async () => {
    mockTrace({});
    renderApp(`${LIST}/t1`);

    const block = within(await screen.findByRole("region", { name: "Metadata" }));
    expect(block.getByText(/No metadata on this trace/)).toBeInTheDocument();
    expect(block.queryByRole("link")).not.toBeInTheDocument();
  });
});
