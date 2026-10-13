/** Pure scroll arithmetic for the rail's navigation (see rail-scroll-area.tsx). */

interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export interface ScrollCues {
  /** More pages above the view. */
  above: boolean;
  /** More pages below the view. */
  below: boolean;
}

/** Which edges of a scroll box hide content. A sub-pixel remainder counts as the end. */
export function scrollCues({ scrollTop, scrollHeight, clientHeight }: ScrollMetrics): ScrollCues {
  return {
    above: scrollTop >= 1,
    below: scrollHeight - clientHeight - scrollTop >= 1,
  };
}

/**
 * The scrollTop that shows an item (offsets within the scroll content) at least `margin` px clear
 * of both edges, moving as little as possible. An item already in view keeps the current position.
 */
export function revealScrollTop(
  view: { scrollTop: number; clientHeight: number },
  item: { top: number; bottom: number },
  margin: number,
): number {
  if (item.top - margin < view.scrollTop) {
    return Math.max(0, item.top - margin);
  }
  if (item.bottom + margin > view.scrollTop + view.clientHeight) {
    return item.bottom + margin - view.clientHeight;
  }
  return view.scrollTop;
}
