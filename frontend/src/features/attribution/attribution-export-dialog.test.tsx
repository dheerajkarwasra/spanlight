import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, authApi, exportsApi, type TraceExport } from "@/lib/api";
import { ME, mockSignedIn, renderApp } from "@/test/render-app";

import { mockAttribution, PAGE, PROJECT_ID, project, response } from "./attribution-test-data";

// Any instant: the window is resolved from the clock when the page renders.
const INSTANT: unknown = expect.any(String);

const ROLLUP = `${PAGE}?group_by=metadata&key=customer_id&range=30d&env=production`;

function accepted(): TraceExport {
  return {
    id: "exp_1",
    kind: "attribution",
    format: "csv",
    filters: { from: "2026-09-16T00:00:00Z", to: "2026-10-16T00:00:00Z" },
    group_by: "metadata",
    key: "customer_id",
    status: "queued",
    row_count: null,
    size_bytes: null,
    error_code: null,
    created_at: "2026-10-16T10:00:00Z",
    completed_at: null,
    expires_at: null,
    download_url: null,
  };
}

async function openDialog() {
  await screen.findByRole("table", { name: "Cost by customer_id" });
  await userEvent.click(screen.getByRole("button", { name: "Export" }));
  return within(await screen.findByRole("dialog", { name: "Export attribution" }));
}

describe("AttributionExportDialog", () => {
  beforeEach(() => {
    mockSignedIn([project(["customer_id"])]);
  });

  it("echoes the group, window and environment, then submits the attribution body with a key", async () => {
    mockAttribution(() => Promise.resolve(response()));
    const create = vi.spyOn(exportsApi, "create").mockResolvedValue(accepted());
    renderApp(ROLLUP);

    const dialog = await openDialog();
    expect(dialog.getByText("16 Sep 2026 – 15 Oct 2026")).toBeInTheDocument();
    expect(dialog.getByText("Last 30 days, as whole UTC days")).toBeInTheDocument();
    expect(dialog.getByText("customer_id")).toBeInTheDocument();
    expect(dialog.getByText("environment: production")).toBeInTheDocument();
    expect(dialog.getByText("Daily rollup, updated every 15 min")).toBeInTheDocument();

    await userEvent.click(dialog.getByRole("button", { name: "Start export" }));

    expect(create).toHaveBeenCalledWith(
      PROJECT_ID,
      {
        kind: "attribution",
        format: "csv",
        group_by: "metadata",
        key: "customer_id",
        filters: { from: INSTANT, to: INSTANT, environment: "production" },
      },
      expect.stringMatching(/^[0-9a-f]{32}$/),
    );
  });

  it("shows the queued state with the key, the window and the format", async () => {
    mockAttribution(() => Promise.resolve(response()));
    vi.spyOn(exportsApi, "create").mockResolvedValue(accepted());
    renderApp(ROLLUP);

    const dialog = await openDialog();
    await userEvent.click(dialog.getByRole("button", { name: "Start export" }));

    const queued = within(await screen.findByRole("dialog", { name: "Export queued" }));
    expect(queued.getByRole("status")).toHaveTextContent("customer_id · 30 days · CSV");
    expect(queued.getByRole("link", { name: "View exports" })).toBeInTheDocument();
  });

  it("sends no key for a trace-name group and reads a 24-hour window live", async () => {
    mockAttribution(() =>
      Promise.resolve(
        response({
          group_by: "trace_name",
          key: null,
          window: { from: "2026-10-15T10:00:00Z", to: "2026-10-16T10:00:00Z" },
          source: "raw",
          updated_within_minutes: 0,
        }),
      ),
    );
    const create = vi.spyOn(exportsApi, "create").mockResolvedValue(accepted());
    renderApp(`${PAGE}?group_by=trace_name&range=24h`);

    await screen.findByRole("table", { name: "Cost by trace name" });
    await userEvent.click(screen.getByRole("button", { name: "Export" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Export attribution" }));
    expect(dialog.getByText("Live, read from stored spans")).toBeInTheDocument();
    expect(dialog.getByText("All environments")).toBeInTheDocument();

    await userEvent.click(dialog.getByRole("button", { name: "Start export" }));
    const [, body] = create.mock.calls[0] ?? [];
    expect(body).toEqual({
      kind: "attribution",
      format: "csv",
      group_by: "trace_name",
      filters: { from: INSTANT, to: INSTANT },
    });
    const queued = within(await screen.findByRole("dialog", { name: "Export queued" }));
    expect(queued.getByRole("status")).toHaveTextContent("Trace name · 24 hours · CSV");
  });

  it("is off while the fetch is held for a key that is not promoted", async () => {
    mockAttribution(() => Promise.resolve(response()));
    renderApp(`${PAGE}?group_by=metadata&key=region&range=30d`);

    await screen.findByText(/region/, { selector: "code" });
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
  });

  it("warns when the server has no object storage and keeps the button on", async () => {
    mockAttribution(() => Promise.resolve(response()));
    vi.spyOn(exportsApi, "create").mockRejectedValue(
      new ApiError(409, { code: "NOT_CONFIGURED", detail: "Exports are not configured." }),
    );
    renderApp(ROLLUP);

    const dialog = await openDialog();
    await userEvent.click(dialog.getByRole("button", { name: "Start export" }));

    expect(await dialog.findByText("Not configured on this server")).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Start export" })).toBeEnabled();
  });

  it("says up front that exports aren't configured and disables Start export", async () => {
    vi.spyOn(authApi, "me").mockResolvedValue({
      ...ME,
      capabilities: { ...ME.capabilities, exports_configured: false },
    });
    mockAttribution(() => Promise.resolve(response()));
    const create = vi.spyOn(exportsApi, "create");
    renderApp(ROLLUP);

    const dialog = await openDialog();
    expect(dialog.getByText("Not configured on this server")).toBeInTheDocument();
    expect(dialog.getByText(/Exports need object storage/)).toHaveTextContent(
      "Exports need object storage. Ask your administrator to set S3_BUCKET, S3_ACCESS_KEY and S3_SECRET_KEY.",
    );
    expect(dialog.getByRole("button", { name: "Start export" })).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });
});
