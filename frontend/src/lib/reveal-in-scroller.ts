interface Edges {
  left: number;
  right: number;
}

/**
 * How far a sideways scroller must move so `item` is fully inside `container`, keeping `gutter`
 * pixels clear of the edge: negative scrolls left, positive right, 0 when it already shows.
 */
export function horizontalReveal(container: Edges, item: Edges, gutter: number): number {
  if (item.left < container.left + gutter) {
    return item.left - container.left - gutter;
  }
  if (item.right > container.right - gutter) {
    return Math.min(item.right - container.right + gutter, item.left - container.left - gutter);
  }
  return 0;
}

/** Scrolls `scroller` sideways (and only sideways) until `item` shows. */
export function revealInScroller(scroller: HTMLElement, item: HTMLElement, gutter = 16): void {
  const delta = horizontalReveal(
    scroller.getBoundingClientRect(),
    item.getBoundingClientRect(),
    gutter,
  );
  if (delta !== 0) {
    scroller.scrollLeft += delta;
  }
}
