import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  authApi,
  insightsApi,
  type Sensitivity,
  type SensitivityPreview,
} from "@/lib/api";
import { makeProject, ME, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PATH = `/${ORG_ID}/proj_1/doctor?tab=sensitivity`;

const ERROR_SPIKE: Sensitivity = {
  detector: "error_spike",
  tunable: true,
  level: "high_recall",
  metric_label: "error rate",
  gates: { balanced: "0.10", high_precision: "0.25" },
  findings_30d: 12,
  updated_at: null,
};

const UNPRICED: Sensitivity = {
  detector: "unpriced_spend",
  tunable: false,
  level: "high_recall",
  metric_label: null,
  gates: null,
  findings_30d: 6,
  updated_at: null,
};

const PREVIEW: SensitivityPreview = {
  detector: "error_spike",
  windows: [
    { days: 7, high_recall: 4, balanced: 2, high_precision: 0 },
    { days: 30, high_recall: 12, balanced: 5, high_precision: 2 },
  ],
};

function asRole(role: "member" | "viewer") {
  vi.spyOn(authApi, "me").mockResolvedValue({
    ...ME,
    memberships: ME.memberships.map((m) => ({ ...m, role })),
  });
}

async function loadedTable() {
  await screen.findAllByText("Error spike", {}, { timeout: 10_000 });
  return screen.getByRole("table", { name: "Sensitivity per check" });
}

async function openDialog() {
  const table = await loadedTable();
  const change = within(table).getByRole("button", { name: "Change sensitivity for Error spike" });
  await userEvent.click(change);
  return {
    change,
    dialog: await screen.findByRole("dialog", { name: "Sensitivity · Error spike" }),
  };
}

// The first test loads the lazy route, which can take a while on a busy machine.
describe("the Doctor Sensitivity tab", { timeout: 15_000 }, () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSignedIn([makeProject("proj_1", "Checkout")]);
    vi.spyOn(insightsApi, "summary").mockResolvedValue({
      open_critical: 0,
      open_warning: 0,
      open_info: 0,
    });
    vi.spyOn(insightsApi, "detectorRuns").mockResolvedValue([]);
    vi.spyOn(insightsApi, "sensitivity").mockResolvedValue({ items: [ERROR_SPIKE, UNPRICED] });
    vi.spyOn(insightsApi, "sensitivityPreview").mockResolvedValue(PREVIEW);
  });

  it("gives an admin a Change button per adjustable check and marks fixed ones", async () => {
    renderApp(PATH);
    const table = await loadedTable();
    expect(screen.getByRole("radio", { name: "Sensitivity" })).toBeChecked();
    const fixed = within(table).getByRole("row", { name: /Unpriced spend/ });
    expect(fixed).toHaveTextContent("Fixed");
    expect(fixed).toHaveTextContent("One setting");
    expect(within(fixed).queryByRole("button")).not.toBeInTheDocument();
    expect(
      within(table).getByRole("button", { name: "Change sensitivity for Error spike" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Only admins and owners can change sensitivity.")).toBeNull();
  });

  it.each(["member", "viewer"] as const)("shows a %s no actions and one note", async (role) => {
    asRole(role);
    renderApp(PATH);
    const table = await loadedTable();
    expect(within(table).queryByRole("columnheader", { name: "Action" })).not.toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: /Change sensitivity/ })).toHaveLength(0);
    expect(screen.getAllByText("Only admins and owners can change sensitivity.")).toHaveLength(1);
  });

  it("previews each setting, warns when stricter, saves and returns focus", async () => {
    let answer: (preview: SensitivityPreview) => void = () => undefined;
    vi.spyOn(insightsApi, "sensitivityPreview").mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    const save = vi
      .spyOn(insightsApi, "setSensitivity")
      .mockResolvedValue({ ...ERROR_SPIKE, level: "balanced" });
    renderApp(PATH);
    const { change, dialog } = await openDialog();

    const group = within(dialog).getByRole("radiogroup", { name: "Setting" });
    expect(within(group).getAllByRole("radio")).toHaveLength(3);
    expect(within(group).getByRole("radio", { name: /High recall/ })).toBeChecked();
    const preview = within(dialog).getByRole("table", { name: "Findings it would have opened" });
    expect(preview).toHaveAttribute("aria-busy", "true");

    answer(PREVIEW);
    await waitFor(() => {
      expect(preview).toHaveAttribute("aria-busy", "false");
    });
    const rows = within(preview)
      .getAllByRole("row")
      .map((row) => [...row.children].map((cell) => cell.textContent.trim()));
    expect(rows).toEqual([
      ["Setting", "Last 7 days", "Last 30 days"],
      ["High recall (current)", "4", "12"],
      ["Balanced", "2", "5"],
      ["High precision", "0", "2"],
    ]);

    const note =
      "Open error spike findings below 10 % resolve after 24 hours without a new detection.";
    expect(within(dialog).queryByText(note)).toBeNull();
    await userEvent.click(within(group).getByRole("radio", { name: /Balanced/ }));
    expect(within(dialog).getByText(note)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Save setting" }));
    expect(save).toHaveBeenCalledWith("proj_1", "error_spike", "balanced");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(change).toHaveFocus();
  });

  it("still saves when the preview fails", async () => {
    vi.spyOn(insightsApi, "sensitivityPreview").mockRejectedValue(
      new ApiError(404, { code: "DETECTOR_NOT_TUNABLE" }),
    );
    const save = vi
      .spyOn(insightsApi, "setSensitivity")
      .mockResolvedValue({ ...ERROR_SPIKE, level: "high_precision" });
    renderApp(PATH);
    const { dialog } = await openDialog();
    expect(await within(dialog).findByText("Preview didn’t load")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("radio", { name: /High precision/ }));
    const button = within(dialog).getByRole("button", { name: "Save setting" });
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(save).toHaveBeenCalledWith("proj_1", "error_spike", "high_precision");
  });

  it("is reached from the Precision tab, which still opens from the URL", async () => {
    vi.spyOn(insightsApi, "precision").mockResolvedValue({
      kinds: [
        {
          kind: "error_spike",
          label: "Error spike",
          right: 18,
          wrong: 2,
          n: 20,
          precision: "0.9000",
          wilson_low: "0.6990",
          wilson_high: "0.9721",
          meets_target: true,
        },
      ],
    });
    renderApp(`/${ORG_ID}/proj_1/doctor?tab=precision`);
    const link = await screen.findByRole(
      "link",
      { name: "Too many wrong findings? Change sensitivity" },
      { timeout: 10_000 },
    );
    expect(screen.getByRole("radio", { name: "Precision" })).toBeChecked();
    await userEvent.click(link);
    expect(await screen.findByRole("table", { name: "Sensitivity per check" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Sensitivity" })).toBeChecked();
  });
});
