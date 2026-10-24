import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError, authApi, projectsApi, type TraceSummary } from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PROJECT_ID = "proj_1";
const LIST = `/${ORG_ID}/${PROJECT_ID}/traces`;

const TRACE = {
  trace_id: "t1",
  name: "run-t1",
  started_at: "2026-10-17T08:00:00Z",
  tags: [],
  models: [],
  scores: [],
  metadata: {},
} as unknown as TraceSummary;

function listed(count: number): TraceSummary[] {
  return Array.from({ length: count }, (_, index) => ({
    ...TRACE,
    trace_id: `t${String(index + 1)}`,
    name: `run-t${String(index + 1)}`,
  }));
}

function mockRole(role: "owner" | "viewer", traces: TraceSummary[]) {
  mockSignedIn([makeProject(PROJECT_ID, "Support")]);
  const membership = ME.memberships[0];
  if (membership) {
    vi.spyOn(authApi, "me").mockResolvedValue({ ...ME, memberships: [{ ...membership, role }] });
  }
  vi.spyOn(projectsApi, "overview").mockRejectedValue(new ApiError(500, {}));
  vi.spyOn(projectsApi, "traces").mockResolvedValue({ items: traces, next_cursor: null });
  renderApp(LIST);
}

async function tickOneRow(role: "owner" | "viewer") {
  mockRole(role, [TRACE]);
  await userEvent.click(await screen.findByRole("checkbox", { name: "Select run-t1 t1" }));
  await screen.findByRole("region", { name: "Selected traces" });
}

describe("the bulk selection bar", () => {
  it("offers Export selection to members who may export", async () => {
    await tickOneRow("owner");
    expect(screen.getByRole("button", { name: "Export selection" })).toBeEnabled();
  });

  it("leaves Export selection out for a viewer", async () => {
    await tickOneRow("viewer");
    expect(screen.queryByRole("button", { name: "Export selection" })).not.toBeInTheDocument();
  });

  it("turns Export selection off past 200 traces and says why", async () => {
    mockRole("owner", listed(201));
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select every trace shown" }),
    );

    const bar = await screen.findByRole("region", { name: "Selected traces" });
    expect(within(bar).getByText("201 traces selected")).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Export selection" })).toBeDisabled();
    expect(within(bar).getByText("Exports take at most 200 selected traces.")).toBeInTheDocument();
  });
});
