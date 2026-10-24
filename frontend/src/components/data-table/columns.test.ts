import { describe, expect, it } from "vitest";

import { gridTemplate, nextSort, visibleColumns, type DataColumn } from "./columns";

function column(id: string, extra: Partial<DataColumn<unknown>> = {}): DataColumn<unknown> {
  return { id, header: id, cell: () => null, width: "100px", ...extra };
}

const COLUMNS = [
  column("name", { width: "200px", sticky: true }),
  column("model", { width: "150px", minContainer: 1100 }),
  column("user", { width: "minmax(160px,1fr)", defaultHidden: true }),
];

function ids(columns: DataColumn<unknown>[]): string[] {
  return columns.map((col) => col.id);
}

describe("visibleColumns", () => {
  it("shows the default columns that fit the container", () => {
    expect(ids(visibleColumns(COLUMNS, {}, 1200))).toEqual(["name", "model"]);
    expect(ids(visibleColumns(COLUMNS, {}, 900))).toEqual(["name"]);
  });

  it("lets a stored choice win over the default and the container", () => {
    expect(ids(visibleColumns(COLUMNS, { model: true, user: true }, 900))).toEqual([
      "name",
      "model",
      "user",
    ]);
    expect(ids(visibleColumns(COLUMNS, { name: false }, 1200))).toEqual(["model"]);
  });

  it("ignores minContainer while the width is not measured yet", () => {
    expect(ids(visibleColumns(COLUMNS, {}, 0))).toEqual(["name", "model"]);
  });
});

describe("gridTemplate", () => {
  it("joins the column tracks in order", () => {
    expect(gridTemplate(COLUMNS)).toBe("200px 150px minmax(160px,1fr)");
  });
});

describe("nextSort", () => {
  it("starts a new key descending, then flips the same key", () => {
    expect(nextSort(undefined, "cost")).toEqual({ key: "cost", order: "desc" });
    expect(nextSort({ key: "duration", order: "asc" }, "cost")).toEqual({
      key: "cost",
      order: "desc",
    });
    expect(nextSort({ key: "cost", order: "desc" }, "cost")).toEqual({ key: "cost", order: "asc" });
    expect(nextSort({ key: "cost", order: "asc" }, "cost")).toEqual({ key: "cost", order: "desc" });
  });
});
