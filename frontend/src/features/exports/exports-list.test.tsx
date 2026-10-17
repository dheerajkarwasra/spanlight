import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { exportsApi, type TraceExport } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PROJECT_ID = "proj_1";

function entry(overrides: Partial<TraceExport>): TraceExport {
  return {
    id: "exp_1",
    kind: "traces",
    format: "csv",
    filters: { from: "2026-10-08T00:00:00Z", to: "2026-10-09T00:00:00Z" },
    group_by: null,
    key: null,
    status: "queued",
    row_count: null,
    size_bytes: null,
    error_code: null,
    created_at: "2026-10-09T10:00:00Z",
    completed_at: null,
    expires_at: null,
    download_url: null,
    ...overrides,
  };
}

describe("ExportsList", () => {
  it("labels an attribution export by its group, beside a trace export's filters", async () => {
    mockSignedIn([makeProject(PROJECT_ID, "Support")]);
    vi.spyOn(exportsApi, "list").mockResolvedValue({
      items: [
        entry({
          id: "exp_a",
          kind: "attribution",
          group_by: "metadata",
          key: "customer_id",
          filters: {
            from: "2026-09-16T00:00:00Z",
            to: "2026-10-16T00:00:00Z",
            environment: "production",
          },
        }),
        entry({ id: "exp_b", kind: "attribution", group_by: "trace_name" }),
        entry({ id: "exp_c" }),
      ],
      next_cursor: null,
    });
    renderApp(`/${ORG_ID}/${PROJECT_ID}/settings/exports`);

    expect(
      (await screen.findAllByText("Attribution by customer_id · environment: production")).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("Attribution by trace name").length).toBeGreaterThan(0);
    expect(screen.getAllByText("All traces").length).toBeGreaterThan(0);
  });

  it("names each download by its kind for assistive technology", async () => {
    mockSignedIn([makeProject(PROJECT_ID, "Support")]);
    vi.spyOn(exportsApi, "list").mockResolvedValue({
      items: [
        entry({
          id: "exp_a",
          kind: "attribution",
          group_by: "metadata",
          key: "customer_id",
          status: "done",
        }),
        entry({ id: "exp_b", kind: "attribution", group_by: "user", status: "done" }),
        entry({ id: "exp_c", format: "jsonl", status: "done" }),
      ],
      next_cursor: null,
    });
    renderApp(`/${ORG_ID}/${PROJECT_ID}/settings/exports`);

    const names = (await screen.findAllByRole("button", { name: /^Download / })).map(
      (button) => button.getAttribute("aria-label") ?? "",
    );
    expect(
      names.some((name) => name.startsWith("Download Attribution CSV export, customer_id, ")),
    ).toBe(true);
    expect(names.some((name) => name.startsWith("Download Attribution CSV export, user, "))).toBe(
      true,
    );
    expect(names.some((name) => name.startsWith("Download JSONL export, "))).toBe(true);
  });
});
