import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { datasetsApi, type Dataset } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

// Each test renders the whole app; under load the first one's cold lazy route chunk took over 5 s.
vi.setConfig({ testTimeout: 20_000 });

const DATASET: Dataset = {
  id: "ds_1",
  name: "support-questions",
  description: null,
  item_count: 50,
  locked_at: null,
  created_at: "2026-10-02T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
};

function renderDetail(dataset: Dataset) {
  mockSignedIn([makeProject("proj_1", "Support")]);
  vi.spyOn(datasetsApi, "get").mockResolvedValue(dataset);
  vi.spyOn(datasetsApi, "items").mockResolvedValue({ items: [], next_cursor: null });
  renderApp(`/${ORG_ID}/proj_1/datasets/ds_1`);
}

describe("Dataset header Run experiment", () => {
  it("links to a new experiment with this dataset picked", async () => {
    renderDetail(DATASET);
    expect(
      await screen.findByRole("link", { name: "Run experiment" }, { timeout: 15_000 }),
    ).toHaveAttribute("href", expect.stringMatching(/\/experiments\/new\?dataset=ds_1$/));
  });

  it("is disabled with its reason while the dataset is empty", async () => {
    renderDetail({ ...DATASET, item_count: 0 });
    expect(
      await screen.findByRole("button", { name: "Run experiment" }, { timeout: 15_000 }),
    ).toBeDisabled();
    expect(screen.getByText("Add items first")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Run experiment" })).not.toBeInTheDocument();
  });
});

// Final review B1: at >= 1024 px the actions row kept its full width and squeezed the name to 0 px.
describe("Dataset header layout", () => {
  it("lets the title column grow and the actions row wrap", async () => {
    renderDetail(DATASET);
    const title = await screen.findByRole(
      "heading",
      { level: 1, name: "support-questions" },
      { timeout: 15_000 },
    );
    const column = title.parentElement?.parentElement;
    expect(column).toHaveClass("min-w-0", "flex-1", "lg:min-w-64");
    expect(column?.nextElementSibling).toHaveClass("flex-wrap", "lg:justify-end");
    expect(column?.nextElementSibling).not.toHaveClass("shrink-0");
  });
});
