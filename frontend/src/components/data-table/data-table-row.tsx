import { ArrowDown, ArrowUp } from "lucide-react";
import type { CSSProperties, MouseEvent, ReactNode, Ref } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { nextSort, type DataColumn, type SortOrder, type SortState } from "./columns";
import type { Density } from "./use-table-prefs";

const ROW_CLASS: Record<Density, string> = {
  comfortable: "h-10 text-label",
  compact: "h-8 text-xs",
};

/** Where the sticky columns sit: `lefts[i]` for visible column i, `width` up to the last one. */
export interface StickyLayout {
  lefts: number[];
  width: number;
}

export interface CellLayout {
  template: string;
  sticky: StickyLayout;
  lastSticky: number;
}

/** The settings rail stays at the right edge; a hairline shows when columns run under it. */
const RAIL_CLASS =
  "sticky right-0 z-[5] group-data-[clipped]/table:shadow-[inset_1px_0_0_var(--color-border)]";

function cellProps<Row>(col: DataColumn<Row>, index: number, layout: CellLayout, bg: string) {
  return {
    className: cn(
      "flex min-w-0 items-center px-3",
      col.align === "end" && "justify-end text-right tabular",
      col.sticky && ["sticky z-[5]", bg],
      index === layout.lastSticky &&
        "group-data-[scrolled]/table:shadow-[inset_-1px_0_0_var(--color-border)]",
    ),
    style: col.sticky ? { left: layout.sticky.lefts[index] ?? 0 } : undefined,
  };
}

const ARIA_SORT = { asc: "ascending", desc: "descending", none: "none" } as const;

interface HeaderRowProps<Row> {
  columns: DataColumn<Row>[];
  layout: CellLayout;
  sort?: SortState & { onChange: (key: string, order: SortOrder) => void };
  settings: ReactNode;
  ref: Ref<HTMLDivElement>;
}

export function HeaderRow<Row>({ columns, layout, sort, settings, ref }: HeaderRowProps<Row>) {
  return (
    <div role="rowgroup" className="sticky top-0 z-10">
      <div
        ref={ref}
        role="row"
        aria-rowindex={1}
        style={{ gridTemplateColumns: layout.template }}
        className="grid h-9 min-w-min border-b border-border bg-surface text-overline text-muted-foreground uppercase"
      >
        {columns.map((col, index) => {
          const sortable = col.sortKey !== undefined && sort !== undefined;
          const order = sortable && col.sortKey === sort.key ? sort.order : undefined;
          return (
            <div
              key={col.id}
              role="columnheader"
              aria-sort={sortable ? ARIA_SORT[order ?? "none"] : undefined}
              // The header's name stays its visible text; "Sort by" belongs to the button only.
              aria-label={sortable ? col.header : undefined}
              {...cellProps(col, index, layout, "bg-surface")}
            >
              {sortable ? (
                <SortButton
                  header={col.header}
                  order={order}
                  onClick={() => {
                    const next = nextSort(sort, col.sortKey ?? "");
                    sort.onChange(next.key, next.order);
                  }}
                />
              ) : (
                (col.headerCell ?? <span className="truncate">{col.header}</span>)
              )}
            </div>
          );
        })}
        {/* A real column (body rows end in an empty cell), so the header row owns only headers. */}
        <div
          role="columnheader"
          className={cn(RAIL_CLASS, "flex items-center justify-end bg-surface pr-1")}
        >
          {settings}
        </div>
      </div>
    </div>
  );
}

function SortButton(props: { header: string; order?: SortOrder; onClick: () => void }) {
  const Arrow = props.order === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={props.onClick}
      className="inline-flex min-h-6 min-w-0 items-center gap-1 rounded-sm uppercase hover:text-foreground"
    >
      <span className="truncate">
        <span className="sr-only">Sort by</span> {props.header}
      </span>
      {props.order ? <Arrow aria-hidden className="size-3 shrink-0" strokeWidth={2.5} /> : null}
    </button>
  );
}

interface BodyRowProps<Row> {
  row: Row;
  index: number;
  columns: DataColumn<Row>[];
  layout: CellLayout;
  density: Density;
  selected: boolean;
  clickable: boolean;
  style?: CSSProperties;
  onClick: (event: MouseEvent<HTMLDivElement>) => void;
}

export function BodyRow<Row>(props: BodyRowProps<Row>) {
  const { row, columns, layout, selected } = props;
  return (
    <div
      role="row"
      aria-rowindex={props.index + 2}
      data-state={selected ? "selected" : undefined}
      style={{ ...props.style, gridTemplateColumns: layout.template }}
      onClick={props.onClick}
      className={cn(
        "group/row grid min-w-min border-b border-border text-foreground transition-colors",
        ROW_CLASS[props.density],
        props.clickable && "cursor-pointer",
        selected ? "bg-surface-selected" : "bg-surface hover:bg-surface-hover",
      )}
    >
      {columns.map((col, index) => (
        <div
          key={col.id}
          role="cell"
          {...cellProps(
            col,
            index,
            layout,
            selected ? "bg-surface-selected" : "bg-surface group-hover/row:bg-surface-hover",
          )}
        >
          {col.cell(row)}
        </div>
      ))}
      <div
        role="cell"
        className={cn(
          RAIL_CLASS,
          selected ? "bg-surface-selected" : "bg-surface group-hover/row:bg-surface-hover",
        )}
      />
    </div>
  );
}

/** Loading placeholder rows shaped like the visible columns. */
export function SkeletonRows<Row>(props: { columns: DataColumn<Row>[]; template: string }) {
  return (
    <div aria-hidden>
      {Array.from({ length: 8 }, (_, row) => (
        <div
          key={row}
          style={{ gridTemplateColumns: props.template }}
          className="grid h-10 min-w-min items-center border-b border-border"
        >
          {props.columns.map((col) => (
            <div key={col.id} className={cn("flex px-3", col.align === "end" && "justify-end")}>
              <Skeleton className="h-3.5 w-3/4" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
