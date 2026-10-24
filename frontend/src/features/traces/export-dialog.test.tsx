import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  exportsApi,
  projectsApi,
  type OverviewMetrics,
  type TraceExport,
  type TraceSummary,
} from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PROJECT_ID = "proj_1";
const LIST = `/${ORG_ID}/${PROJECT_ID}/traces`;

const ACCEPTED: TraceExport = {
  id: "exp_1",
  kind: "traces",
  format: "csv",
  filters: { from: "2026-10-16T00:00:00Z", to: "2026-10-17T00:00:00Z" },
  group_by: null,
  key: null,
  status: "queued",
  row_count: null,
  size_bytes: null,
  error_code: null,
  created_at: "2026-10-17T10:00:00Z",
  completed_at: null,
  expires_at: null,
  download_url: null,
};

beforeEach(() => {
  mockSignedIn([makeProject(PROJECT_ID, "Support")]);
  vi.spyOn(projectsApi, "filters").mockResolvedValue({
    environments: [],
    releases: [],
    models: [],
    tags: [],
    score_names: [],
    metadata_keys: ["customer_id"],
    trace_names: [],
  });
  vi.spyOn(projectsApi, "traces").mockResolvedValue({ items: [], next_cursor: null });
  // Only the window's trace count is read from the overview here.
  vi.spyOn(projectsApi, "overview").mockResolvedValue({
    current: { traces: 1284 },
  } as unknown as OverviewMetrics);
});

function trace(id: string, startedAt: string): TraceSummary {
  return {
    trace_id: id,
    name: `run-${id}`,
    environment: "production",
    release: null,
    external_user_id: null,
    session_id: null,
    tags: [],
    started_at: startedAt,
    ended_at: startedAt,
    duration_ms: 0,
    span_count: 1,
    error_count: 0,
    input_tokens: 1,
    output_tokens: 1,
    cost_usd: null,
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

async function startCsvExport() {
  await userEvent.click(await screen.findByRole("button", { name: "Export" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Export traces" }));
  await userEvent.click(dialog.getByRole("radio", { name: /CSV/ }));
  await userEvent.click(dialog.getByRole("button", { name: "Start export" }));
  return dialog;
}

describe("the trace export dialog", () => {
  it("keeps the row count and format in the queued state", async () => {
    vi.spyOn(exportsApi, "create").mockResolvedValue(ACCEPTED);
    renderApp(LIST);

    await startCsvExport();

    const queued = within(await screen.findByRole("dialog", { name: "Export queued" }));
    expect(queued.getByRole("status")).toHaveTextContent("1,284 traces · CSV");
  });

  it("sends the list's metadata pair and no longer lists it as not applied", async () => {
    const create = vi.spyOn(exportsApi, "create").mockResolvedValue(ACCEPTED);
    renderApp(`${LIST}?metadata_key=customer_id&metadata_value=acme&name=summarize`);

    await userEvent.click(await screen.findByRole("button", { name: "Export" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Export traces" }));
    expect(dialog.getByText(/Exports don’t apply trace name yet/)).toBeInTheDocument();
    expect(dialog.queryByText(/apply.*metadata/)).not.toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Start export" }));

    const [, body] = create.mock.calls[0] ?? [];
    expect(body).toMatchObject({
      filters: { metadata_key: "customer_id", metadata_value: "acme" },
    });
    expect(body && "filters" in body ? body.filters : {}).not.toHaveProperty("name");
  });

  it("exports the ticked traces by id, over the window from the first to the last", async () => {
    const create = vi.spyOn(exportsApi, "create").mockResolvedValue(ACCEPTED);
    vi.spyOn(projectsApi, "traces").mockResolvedValue({
      items: [
        trace("t2", "2026-10-17T09:30:00.250Z"),
        trace("t1", "2026-10-17T08:00:00Z"),
        trace("t3", "2026-10-17T07:00:00Z"),
      ],
      next_cursor: null,
    });
    renderApp(`${LIST}?model=gpt-4.1`);

    await userEvent.click(await screen.findByRole("checkbox", { name: "Select run-t1 t1" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select run-t2 t2" }));
    await userEvent.click(screen.getByRole("button", { name: "Export selection" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Export traces" }));
    expect(dialog.getByText("2 selected traces")).toBeInTheDocument();
    expect(dialog.queryByText("model: gpt-4.1")).not.toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Start export" }));

    const [, body] = create.mock.calls[0] ?? [];
    expect(body && "filters" in body ? body.filters : {}).toEqual({
      from: "2026-10-17T08:00:00.000Z",
      // The end is exclusive, so it lies just past the last trace's start.
      to: "2026-10-17T09:30:00.251Z",
      trace_ids: ["t1", "t2"],
    });
  });
});
