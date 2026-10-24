import { useLayoutEffect, useState, type RefObject } from "react";

export interface WindowListOffsets {
  /** The list's offset from the top of what scrolls (the document, or `scrollElement`'s content). */
  scrollMargin: number;
  /**
   * Where the sticky bars above the list end (the phone app bar, a trace's summary bar), so a
   * scrolled-to row clears them (WCAG 2.4.11).
   */
  scrollPaddingStart: number;
}

/** Where a stuck bar ends, from the scroller's top; 0 when it isn't sticky (hidden, or desktop). */
function stuckBottom(bar: HTMLElement): number {
  const style = getComputedStyle(bar);
  if (style.position !== "sticky") {
    return 0;
  }
  return (Number.parseFloat(style.top) || 0) + bar.getBoundingClientRect().height;
}

/**
 * The bars that can cover the list. In a scroll box (the peek): its sticky bars that come before
 * the list, not one after it (a sticky inspector beside it). On the page: the phone app bar and
 * the caller's own `stickyBar`; the rest of the document isn't scanned, so the desktop rail (a
 * sticky column beside the content) never counts.
 */
function coveringBars(
  list: HTMLElement,
  scrollElement: HTMLElement | null | undefined,
  stickyBar: HTMLElement | null | undefined,
): HTMLElement[] {
  if (!scrollElement) {
    return [...document.querySelectorAll("header"), ...(stickyBar ? [stickyBar] : [])];
  }
  return [...scrollElement.querySelectorAll<HTMLElement>('header, [class*="sticky"]')].filter(
    (bar) =>
      !bar.contains(list) &&
      (list.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_PRECEDING) !== 0,
  );
}

/**
 * Offsets for a virtualizer whose list is not the scroll box: the page (or `scrollElement`)
 * scrolls, not the list. Re-measured whenever the page's or the box's content changes size,
 * since content above the list may grow, shrink or rewrap.
 */
export function useScrollMargin(
  ref: RefObject<HTMLElement | null>,
  enabled: boolean,
  scrollElement?: HTMLElement | null,
  /** The page's own sticky bar over the list (a trace's summary bar); ignored in a scroll box. */
  stickyBar?: RefObject<HTMLElement | null>,
): WindowListOffsets {
  const [scrollMargin, setScrollMargin] = useState(0);
  const [scrollPaddingStart, setScrollPaddingStart] = useState(0);
  useLayoutEffect(() => {
    if (!enabled) {
      return;
    }
    function measure() {
      if (ref.current) {
        const origin = scrollElement
          ? scrollElement.getBoundingClientRect().top - scrollElement.scrollTop
          : -window.scrollY;
        setScrollMargin(ref.current.getBoundingClientRect().top - origin);
        const bars = coveringBars(ref.current, scrollElement, stickyBar?.current);
        setScrollPaddingStart(Math.max(0, ...bars.map(stuckBottom)));
      }
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    for (const child of scrollElement?.children ?? []) {
      observer.observe(child);
    }
    return () => {
      observer.disconnect();
    };
  }, [ref, enabled, scrollElement, stickyBar]);
  return { scrollMargin, scrollPaddingStart };
}
