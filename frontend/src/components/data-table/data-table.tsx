import type { LinkProps } from "@tanstack/react-router";
import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

import {
  gridTemplate,
  visibleColumns,
  type DataColumn,
  type SortOrder,
  type SortState,
} from "./columns";
import { DataTableBody } from "./data-table-body";
import { HeaderRow, SkeletonRows, type StickyLayout } from "./data-table-row";
import { TableSettingsMenu } from "./table-settings-menu";
import { useTablePrefs } from "./use-table-prefs";

/** The trailing track that holds the Table settings button in the header. */
const SETTINGS_TRACK = "2.5rem";

/** Marks the sides where columns run under a sticky edge: the frozen columns, the settings rail. */
function markEdges(box: HTMLElement) {
  box.toggleAttribute("data-scrolled", box.scrollLeft > 0);
  box.toggleAttribute("data-clipped", box.scrollLeft + box.clientWidth < box.scrollWidth - 1);
}

export interface DataTableProps<Row> {
  /** Storage key for the column choices: `spanlight.columns.<id>`. */
  id: string;
  label: string;
  columns: DataColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** Plain left clicks on the row; ignored on links, buttons and inputs and with modifier keys. */
  onRowClick?: (row: Row, event: MouseEvent<HTMLDivElement>) => void;
  /** Where a plain row click goes when there is no `onRowClick`. */
  rowHref?: (row: Row) => LinkProps;
  rowState?: (row: Row) => "selected" | undefined;
  sort?: SortState & { onChange: (key: string, order: SortOrder) => void };
  footer?: ReactNode;
  empty: ReactNode;
  /** True while rows load; with rows already shown they dim until the new ones arrive. */
  loading: boolean;
  /** CSS length above the table; the box is at most the viewport minus this (default 9rem). */
  scrollOffset?: string;
}

/**
 * The shared data table (spec §3.1): an ARIA table on CSS grid in its own scroll box, with a
 * sticky header and identity column, sortable headers, per-viewer columns and density, and a
 * virtual body for long lists. Phones get the page's tile list instead.
 */
export function DataTable<Row>(props: DataTableProps<Row>) {
  const { rows, loading } = props;
  const prefs = useTablePrefs(props.id);
  const boxRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const [boxWidth, setBoxWidth] = useState(0);
  const [sticky, setSticky] = useState<StickyLayout>({ lefts: [], width: 0 });
  const showEmpty = !loading && rows.length === 0;
  const columns = visibleColumns(props.columns, prefs.overrides, boxWidth);
  const template = `${gridTemplate(columns)} ${SETTINGS_TRACK}`;
  const layout = { template, sticky, lastSticky: columns.findLastIndex((col) => col.sticky) };

  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    setBoxWidth(box.clientWidth);
    const observer = new ResizeObserver(([entry]) => {
      setBoxWidth(entry?.contentRect.width ?? 0);
    });
    observer.observe(box);
    return () => {
      observer.disconnect();
    };
    // The box mounts again when the empty state gives way to rows.
  }, [showEmpty]);

  // Sticky offsets come from the resolved tracks, which don't move while scrolling sideways.
  useLayoutEffect(() => {
    const header = headerRef.current;
    if (!header) return;
    if (boxRef.current) markEdges(boxRef.current);
    const tracks = getComputedStyle(header).gridTemplateColumns.split(" ").map(parseFloat);
    const next: StickyLayout = { lefts: [], width: 0 };
    let left = 0;
    columns.forEach((col, index) => {
      next.lefts.push(left);
      left += tracks[index] || 0;
      if (col.sticky) next.width = left;
    });
    setSticky((prev) =>
      prev.width === next.width && prev.lefts.join() === next.lefts.join() ? prev : next,
    );
  }, [boxWidth, template, columns]);

  const hideable = props.columns.filter((col) => col.hideable);
  const shownIds = new Set(columns.map((col) => col.id));

  return (
    <div className="hidden min-w-0 flex-col md:flex">
      {showEmpty ? (
        props.empty
      ) : (
        <div
          ref={boxRef}
          role="table"
          aria-label={props.label}
          // Rows are numbered from 1, the header included, so a virtualized body still reads right.
          aria-rowcount={rows.length + 1}
          aria-busy={loading || undefined}
          onScroll={(event) => {
            markEdges(event.currentTarget);
          }}
          style={
            {
              "--table-offset": props.scrollOffset,
              scrollPaddingLeft: sticky.width,
              scrollPaddingRight: SETTINGS_TRACK,
            } as CSSProperties
          }
          className={cn(
            "group/table relative max-h-[calc(100dvh-var(--table-offset,9rem))] scroll-pt-9 overflow-auto bg-surface",
            loading && rows.length > 0 && "opacity-60 transition-opacity",
          )}
        >
          <HeaderRow
            ref={headerRef}
            columns={columns}
            layout={layout}
            sort={props.sort}
            settings={
              <TableSettingsMenu
                columns={hideable}
                isVisible={(colId) => shownIds.has(colId)}
                setVisible={prefs.setVisible}
                density={prefs.density}
                setDensity={prefs.setDensity}
              />
            }
          />
          {loading && rows.length === 0 ? (
            <SkeletonRows columns={columns} template={template} />
          ) : (
            <DataTableBody
              boxRef={boxRef}
              rows={rows}
              rowKey={props.rowKey}
              columns={columns}
              layout={layout}
              density={prefs.density}
              rowState={props.rowState}
              onRowClick={props.onRowClick}
              rowHref={props.rowHref}
            />
          )}
        </div>
      )}
      {props.footer}
    </div>
  );
}
