import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { datasetsApi, type Dataset, type DatasetItem } from "@/lib/api";
import { makeProject, mockSignedIn, ORG_ID, renderApp } from "@/test/render-app";

const PROJECT_ID = "proj_1";

function dataset(index: number): Dataset {
  return {
    id: `ds_${index}`,
    name: `dataset-${index}`,
    description: index === 1 ? "Golden answers" : null,
    item_count: index * 10,
    locked_at: null,
    created_at: "2026-10-02T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
  };
}

function item(index: number): DatasetItem {
  return {
    id: `item_${index}`,
    input: `question ${index}`,
    expected_output: index === 1 ? null : `answer ${index}`,
    metadata: { topic: "billing" },
    source_trace_id: index === 1 ? "trace_abcdef123456" : null,
    source_span_id: null,
    created_at: "2026-10-02T00:00:00Z",
  };
}

afterEach(() => {
  window.localStorage.clear();
});

describe("Dataset list table", () => {
  it("renders the Datasets table with name, items and updated columns", async () => {
    mockSignedIn([makeProject(PROJECT_ID, "Support")]);
    vi.spyOn(datasetsApi, "list").mockResolvedValue({
      items: [dataset(1), dataset(2)],
      next_cursor: null,
    });
    renderApp(`/${ORG_ID}/${PROJECT_ID}/datasets`);

    const table = await screen.findByRole("table", { name: "Datasets" });
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent);
    expect(headers.slice(0, 3)).toEqual(["Dataset", "Items", "Updated"]);
    expect(within(table).getByRole("columnheader", { name: "Dataset" })).toHaveClass("sticky");
    expect(within(table).getByRole("link", { name: /dataset-1/ })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/datasets\/ds_1$/),
    );
    expect(within(table).getByText("Golden answers")).toBeInTheDocument();
    // The phone tiles stay in the page for widths below md.
    expect(screen.getAllByRole("link", { name: /dataset-1/ })).toHaveLength(2);
  });

  it("opens a dataset on a plain row click", async () => {
    mockSignedIn([makeProject(PROJECT_ID, "Support")]);
    vi.spyOn(datasetsApi, "list").mockResolvedValue({ items: [dataset(2)], next_cursor: null });
    vi.spyOn(datasetsApi, "get").mockResolvedValue(dataset(2));
    vi.spyOn(datasetsApi, "items").mockResolvedValue({ items: [], next_cursor: null });
    const router = renderApp(`/${ORG_ID}/${PROJECT_ID}/datasets`);

    const table = await screen.findByRole("table", { name: "Datasets" });
    fireEvent.click(within(table).getByText("20"));
    await vi.waitFor(() => {
      expect(router.state.location.pathname).toMatch(/\/datasets\/ds_2$/);
    });
  });

  it("appends the next page on Load more", async () => {
    mockSignedIn([makeProject(PROJECT_ID, "Support")]);
    const list = vi
      .spyOn(datasetsApi, "list")
      .mockResolvedValueOnce({ items: [dataset(1)], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [dataset(2)], next_cursor: null });
    renderApp(`/${ORG_ID}/${PROJECT_ID}/datasets`);

    const table = await screen.findByRole("table", { name: "Datasets" });
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await within(table).findByRole("link", { name: /dataset-2/ })).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: /dataset-1/ })).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith(PROJECT_ID, { limit: 50, cursor: "c1" });
  });
});

describe("Dataset items table", () => {
  function mockDetail() {
    mockSignedIn([makeProject(PROJECT_ID, "Support")]);
    vi.spyOn(datasetsApi, "get").mockResolvedValue(dataset(1));
    return vi.spyOn(datasetsApi, "items");
  }

  it("renders the items table with hideable metadata and source columns", async () => {
    mockDetail().mockResolvedValue({ items: [item(1), item(2)], next_cursor: null });
    renderApp(`/${ORG_ID}/${PROJECT_ID}/datasets/ds_1`);

    const table = await screen.findByRole("table", { name: "Dataset items" });
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent);
    expect(headers.slice(0, 5)).toEqual(["#", "Input", "Expected output", "Metadata", "Source"]);
    expect(within(table).getByRole("columnheader", { name: "#" })).toHaveClass("sticky");
    expect(
      within(table).getByText("Not set. Add an expected output to score this item."),
    ).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: /trace_ab/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Table settings" }));
    expect(await screen.findByRole("menuitemcheckbox", { name: "Metadata" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "Source" }));
    expect(within(table).queryByRole("columnheader", { name: "Source" })).not.toBeInTheDocument();
  });

  it("opens the item editor on a row click and from the number button", async () => {
    mockDetail().mockResolvedValue({ items: [item(1), item(2)], next_cursor: null });
    renderApp(`/${ORG_ID}/${PROJECT_ID}/datasets/ds_1`);

    const table = await screen.findByRole("table", { name: "Dataset items" });
    fireEvent.click(within(table).getByText("question 2"));
    expect(await screen.findByRole("heading", { name: "Edit item 2" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    await userEvent.click(within(table).getByRole("button", { name: "Edit item 1" }));
    expect(await screen.findByRole("heading", { name: "Edit item 1" })).toBeInTheDocument();
  });

  it("appends the next page of items on Load more", async () => {
    mockDetail()
      .mockResolvedValueOnce({ items: [item(1)], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [item(2)], next_cursor: null });
    renderApp(`/${ORG_ID}/${PROJECT_ID}/datasets/ds_1`);

    const table = await screen.findByRole("table", { name: "Dataset items" });
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await within(table).findByText("question 2")).toBeInTheDocument();
    expect(within(table).getByText("question 1")).toBeInTheDocument();
  });
});
