import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { exportsApi, projectsApi, type OverviewMetrics, type TraceExport } from "@/lib/api";
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
});
