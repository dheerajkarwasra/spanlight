import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { DataTable, type DataColumn } from "./index";

interface Row {
  id: string;
  name: string;
  model: string;
  cost: string;
}

function makeRows(count: number): Row[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `r${index + 1}`,
    name: `row-${index + 1}`,
    model: "gpt-4.1",
    cost: "0.01",
  }));
}

const COLUMNS: DataColumn<Row>[] = [
  {
    id: "name",
    header: "Name",
    width: "200px",
    sticky: true,
    cell: (row) => <a href={`#${row.id}`}>{row.name}</a>,
  },
  {
    id: "model",
    header: "Model",
    width: "150px",
    hideable: true,
    cell: (row) => (
      <button type="button" onClick={() => undefined}>
        {row.model}
      </button>
    ),
  },
  {
    id: "cost",
    header: "Cost",
    width: "88px",
    align: "end",
    sortKey: "cost_usd",
    hideable: true,
    cell: (row) => <span>{row.cost}</span>,
  },
];

type TableProps = Partial<ComponentProps<typeof DataTable<Row>>>;

/** Holds the sort in state, as a page would; `props.sort.onChange` spies on the changes. */
function Harness({ sort: spy, ...props }: TableProps) {
  const [sort, setSort] = useState<{ key: string; order: "asc" | "desc" }>({
    key: "started_at",
    order: "desc",
  });
  return (
    <DataTable<Row>
      id="test"
      label="Things"
      columns={COLUMNS}
      rows={makeRows(3)}
      rowKey={(row) => row.id}
      empty={<p>Nothing yet</p>}
      loading={false}
      {...props}
      sort={{
        ...sort,
        onChange: (key, order) => {
          spy?.onChange(key, order);
          setSort({ key, order });
        },
      }}
    />
  );
}

/** Renders under a real router: rows can link and navigate. */
function renderTable(props: TableProps = {}) {
  const rootRoute = createRootRoute({
    component: () => (
      <TooltipProvider>
        <Harness {...props} />
      </TooltipProvider>
    ),
  });
  const anyPath = createRoute({ getParentRoute: () => rootRoute, path: "$" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([anyPath]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return render(<RouterProvider router={router as never} />);
}

async function openSettings() {
  await userEvent.click(await screen.findByRole("button", { name: "Table settings" }));
}

afterEach(() => {
  window.localStorage.clear();
});

describe("DataTable", () => {
  it("is an ARIA table that counts the header row", async () => {
    renderTable();
    const table = await screen.findByRole("table", { name: "Things" });
    expect(table).toHaveAttribute("aria-rowcount", "4");
    expect(within(table).getAllByRole("row")[1]).toHaveAttribute("aria-rowindex", "2");
    // The header row owns only column headers, as many as each body row has cells.
    const [headerRow, firstRow] = within(table).getAllByRole("row");
    const headerCells = Array.from(headerRow?.children ?? []);
    expect(headerCells.every((cell) => cell.getAttribute("role") === "columnheader")).toBe(true);
    expect(headerCells).toHaveLength(firstRow?.querySelectorAll('[role="cell"]').length ?? -1);
    // The identity column sticks to the left in the header and in every row.
    expect(screen.getByRole("columnheader", { name: "Name" })).toHaveClass("sticky");
    expect(within(table).getAllByRole("cell")[0]).toHaveClass("sticky", "bg-surface");
    // The settings column is a rail at the right edge of every row, so it never covers only a
    // header while the cells under it stay readable.
    const rail = (row: HTMLElement | undefined) => row?.lastElementChild;
    expect(rail(headerRow)).toHaveClass("sticky", "right-0", "bg-surface");
    expect(rail(firstRow)).toHaveClass("sticky", "right-0", "bg-surface");
  });

  it("sorts descending first, then ascending, and announces it on the header", async () => {
    const onChange = vi.fn();
    renderTable({ sort: { key: "started_at", order: "desc", onChange } });
    const header = await screen.findByRole("columnheader", { name: "Cost" });
    expect(header).toHaveAttribute("aria-sort", "none");

    await userEvent.click(screen.getByRole("button", { name: "Sort by Cost" }));
    expect(onChange).toHaveBeenLastCalledWith("cost_usd", "desc");
    expect(header).toHaveAttribute("aria-sort", "descending");

    await userEvent.click(screen.getByRole("button", { name: "Sort by Cost" }));
    expect(onChange).toHaveBeenLastCalledWith("cost_usd", "asc");
    expect(header).toHaveAttribute("aria-sort", "ascending");
  });

  it("hides a column from Table settings and remembers it after a remount", async () => {
    const { unmount } = renderTable();
    await openSettings();
    await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Model" }));
    expect(screen.queryByRole("columnheader", { name: "Model" })).not.toBeInTheDocument();
    unmount();

    renderTable();
    await screen.findByRole("table", { name: "Things" });
    expect(screen.queryByRole("columnheader", { name: "Model" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Cost" })).toBeInTheDocument();
  });

  it("switches row density", async () => {
    renderTable();
    const table = await screen.findByRole("table", { name: "Things" });
    expect(within(table).getAllByRole("row")[1]).toHaveClass("h-10");
    await openSettings();
    await userEvent.click(await screen.findByRole("menuitemradio", { name: "Compact" }));
    expect(within(table).getAllByRole("row")[1]).toHaveClass("h-8");
  });

  it("still toggles columns and density when storage throws", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderTable();
    const table = await screen.findByRole("table", { name: "Things" });
    await openSettings();
    await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Model" }));
    expect(screen.queryByRole("columnheader", { name: "Model" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Compact" }));
    expect(within(table).getAllByRole("row")[1]).toHaveClass("h-8");
  });

  it("calls onRowClick only for plain clicks outside links and buttons", async () => {
    const onRowClick = vi.fn();
    renderTable({ onRowClick });
    const table = await screen.findByRole("table", { name: "Things" });
    const row = within(table).getAllByRole("row")[1] as HTMLElement;

    fireEvent.click(within(row).getByRole("link", { name: "row-1" }));
    fireEvent.click(within(row).getByRole("button", { name: "gpt-4.1" }));
    fireEvent.click(within(row).getByText("0.01"), { metaKey: true });
    expect(onRowClick).not.toHaveBeenCalled();

    fireEvent.click(within(row).getByText("0.01"));
    expect(onRowClick).toHaveBeenCalledTimes(1);
    expect(onRowClick.mock.calls[0]?.[0]).toMatchObject({ id: "r1" });
  });

  it("shows the empty state when there are no rows", async () => {
    renderTable({ rows: [] });
    expect(await screen.findByText("Nothing yet")).toBeInTheDocument();
  });
});

describe("DataTable with 300 rows", () => {
  it("renders a window and keeps the focused row mounted when it scrolls away", async () => {
    // jsdom has no layout: give the scroll box a height so the virtualizer has a window.
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    renderTable({ rows: makeRows(300) });
    const table = await screen.findByRole("table", { name: "Things" });
    expect(table).toHaveAttribute("aria-rowcount", "301");
    expect(within(table).getAllByRole("row").length).toBeLessThan(100);

    const link = within(table).getByRole("link", { name: "row-1" });
    link.focus();
    Object.defineProperty(table, "scrollTop", { configurable: true, value: 10_000 });
    fireEvent.scroll(table);

    expect(within(table).queryByText("row-2")).not.toBeInTheDocument();
    expect(link).toBeInTheDocument();
    expect(document.activeElement).toBe(link);
  });
});
