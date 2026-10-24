import {
  defaultRangeExtractor,
  useVirtualizer,
  useWindowVirtualizer,
  type Range,
} from "@tanstack/react-virtual";
import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

import { useScrollMargin } from "./use-scroll-margin";
import { spanRowId } from "./waterfall-items";

/** A row is `h-10` plus the `gap-0.5` between rows. */
const ROW_STRIDE_PX = 42;

interface Options {
  count: number;
  enabled: boolean;
  /** Item index of a span row; -1 when it isn't shown. */
  indexOf: (spanId: string | null) => number;
  /** The roving-tabindex row: always mounted, so Tab and screen readers always find it. */
  focusableId: string | null;
  selectedSpanId: string | null;
  onSelect: (spanId: string) => void;
  treeRef: RefObject<HTMLElement | null>;
  /** The box that scrolls the tree (the peek sheet, the phone tree pane); the window otherwise. */
  scrollElement: HTMLElement | null | undefined;
  /** The page's sticky bar over the tree, so scrolled-to rows clear it. */
  stickyBar: RefObject<HTMLElement | null> | undefined;
}

/**
 * Virtualizes the waterfall's rows against the window or, when given, a scroll box, and keeps
 * the selection in view: `focusRow` selects, scrolls to and focuses a row, even one outside the
 * rendered window.
 */
export function useTreeVirtualizer(options: Options) {
  const { enabled, indexOf, selectedSpanId, scrollElement } = options;
  const { scrollMargin, scrollPaddingStart } = useScrollMargin(
    options.treeRef,
    enabled,
    scrollElement,
    options.stickyBar,
  );
  const focusIndex = indexOf(options.focusableId);
  const shared = {
    count: options.count,
    estimateSize: () => ROW_STRIDE_PX,
    overscan: 10,
    scrollMargin,
    scrollPaddingStart,
    rangeExtractor: (range: Range) => {
      const indexes = defaultRangeExtractor(range);
      return focusIndex < 0 || indexes.includes(focusIndex)
        ? indexes
        : [...indexes, focusIndex].sort((a, b) => a - b);
    },
  };
  const inBox = Boolean(scrollElement);
  const windowVirtualizer = useWindowVirtualizer({ ...shared, enabled: enabled && !inBox });
  // eslint-disable-next-line react-hooks/incompatible-library
  const boxVirtualizer = useVirtualizer({
    ...shared,
    enabled: enabled && inBox,
    getScrollElement: () => scrollElement ?? null,
  });
  const virtualizer = inBox ? boxVirtualizer : windowVirtualizer;
  const pendingFocus = useRef<string | null>(null);

  // A span selected from outside (the URL, a "Select span" link) scrolls into view; focus stays put.
  // Not on mount: opening a trace shouldn't jump the page to its first row.
  const shownSelection = useRef(selectedSpanId);
  useEffect(() => {
    if (shownSelection.current === selectedSpanId) {
      return;
    }
    shownSelection.current = selectedSpanId;
    const index = indexOf(selectedSpanId);
    if (index < 0 || !selectedSpanId) {
      return;
    }
    if (enabled) {
      virtualizer.scrollToIndex(index, { align: "auto" });
    } else {
      document.getElementById(spanRowId(selectedSpanId))?.scrollIntoView({ block: "nearest" });
    }
  });

  useLayoutEffect(() => {
    // A row reached by keyboard outside the rendered window mounts on this render.
    if (pendingFocus.current !== null) {
      const element = document.getElementById(spanRowId(pendingFocus.current));
      if (element) {
        element.focus();
        pendingFocus.current = null;
      }
    }
  });

  function focusRow(spanId: string) {
    options.onSelect(spanId);
    if (enabled) {
      virtualizer.scrollToIndex(indexOf(spanId), { align: "auto" });
    }
    const element = document.getElementById(spanRowId(spanId));
    if (element) {
      element.focus();
    } else {
      pendingFocus.current = spanId;
    }
  }

  return { virtualizer, scrollMargin, focusRow };
}
