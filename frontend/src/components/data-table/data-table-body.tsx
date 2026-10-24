import { useNavigate, type LinkProps } from "@tanstack/react-router";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import {
  useLayoutEffect,
  useState,
  type CSSProperties,
  type FocusEvent,
  type MouseEvent,
  type RefObject,
} from "react";

import type { DataColumn } from "./columns";
import { BodyRow, type CellLayout } from "./data-table-row";
import { HEADER_PX, ROW_PX, type Density } from "./use-table-prefs";

/** Above this many rows only the rows in view (plus overscan) are rendered. */
export const VIRTUALIZE_ROWS = 100;
const INTERACTIVE = "a, button, input, [role='button'], [tabindex]";

interface DataTableBodyProps<Row> {
  /** The table's scroll box, which the virtualizer scrolls. */
  boxRef: RefObject<HTMLDivElement | null>;
  rows: Row[];
  rowKey: (row: Row) => string;
  columns: DataColumn<Row>[];
  layout: CellLayout;
  density: Density;
  rowState?: (row: Row) => "selected" | undefined;
  onRowClick?: (row: Row, event: MouseEvent<HTMLDivElement>) => void;
  rowHref?: (row: Row) => LinkProps;
}

export function DataTableBody<Row>(props: DataTableBodyProps<Row>) {
  const { rows, rowKey, onRowClick, rowHref } = props;
  const navigate = useNavigate();
  const virtual = rows.length > VIRTUALIZE_ROWS;
  const stride = ROW_PX[props.density];
  // The row holding focus stays mounted when it scrolls out of view, so focus is never dropped.
  // Kept by key: rows loaded or reordered above it must not hand the slot to another row.
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const focusedIndex = virtual ? rows.findIndex((row) => rowKey(row) === focusedKey) : -1;
  // The app doesn't build with the React Compiler; the virtualizer re-renders on scroll by design.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    enabled: virtual,
    getScrollElement: () => props.boxRef.current,
    estimateSize: () => stride,
    overscan: 10,
    scrollMargin: HEADER_PX,
    rangeExtractor: (range) => {
      const indexes = defaultRangeExtractor(range);
      return focusedIndex < 0 || indexes.includes(focusedIndex)
        ? indexes
        : [...indexes, focusedIndex].sort((a, b) => a - b);
    },
  });
  // A density change changes the stride; the virtualizer caches sizes until told.
  useLayoutEffect(() => {
    virtualizer.measure();
  }, [virtualizer, stride]);

  function trackFocus(event: FocusEvent<HTMLDivElement>) {
    const row = event.target.closest("[aria-rowindex]");
    const index = row ? Number(row.getAttribute("aria-rowindex")) - 2 : -1;
    const focused = rows[index];
    setFocusedKey(focused === undefined ? null : rowKey(focused));
  }

  function clickRow(event: MouseEvent<HTMLDivElement>, row: Row) {
    // Modifier and middle clicks belong to the row's link (new tab, new window).
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    // Let real links/buttons inside the row, and text selection, behave normally.
    const interactive = (event.target as HTMLElement).closest(INTERACTIVE);
    if (interactive && event.currentTarget.contains(interactive)) return;
    if (window.getSelection()?.toString()) return;
    if (onRowClick) onRowClick(row, event);
    else if (rowHref) void navigate(rowHref(row));
  }

  const visible: { index: number; style?: CSSProperties }[] = virtual
    ? virtualizer.getVirtualItems().map((item) => ({
        index: item.index,
        style: {
          position: "absolute",
          insetInline: 0,
          top: 0,
          transform: `translateY(${item.start - HEADER_PX}px)`,
        },
      }))
    : rows.map((_, index) => ({ index }));

  return (
    <div
      role="rowgroup"
      // Only a virtualized body unmounts rows; a short list needn't re-render on every Tab.
      onFocus={virtual ? trackFocus : undefined}
      onBlur={
        virtual
          ? (event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) setFocusedKey(null);
            }
          : undefined
      }
      className={virtual ? "relative" : undefined}
      style={virtual ? { height: virtualizer.getTotalSize() } : undefined}
    >
      {visible.map(({ index, style }) => {
        const row = rows[index];
        if (row === undefined) return null;
        return (
          <BodyRow
            key={rowKey(row)}
            row={row}
            index={index}
            columns={props.columns}
            layout={props.layout}
            density={props.density}
            selected={props.rowState?.(row) === "selected"}
            clickable={onRowClick !== undefined || rowHref !== undefined}
            style={style}
            onClick={(event) => {
              clickRow(event, row);
            }}
          />
        );
      })}
    </div>
  );
}
