/** Layout classes for `TraceView` (spec §6): panes by container width, sticky offsets. */

/** Container width (px) from which the tree and the inspector sit side by side (`@3xl`). */
export const TWO_PANES_FROM = 768;

/** Two panes from `@3xl`: 40/60, then 45/55 from `@5xl`. Below, the tree stacks over the inspector. */
export const PANES =
  "grid grid-cols-1 gap-4 @3xl:grid-cols-[minmax(0,40fr)_minmax(0,60fr)] @3xl:items-start @5xl:grid-cols-[minmax(0,45fr)_minmax(0,55fr)]";
/** Stacked, the waterfall caps its own rows at 220 px (`capRows`), below its toolbar. */
export const TREE_PANE = "min-w-0";
/** Side by side, the inspector stays in view (scrolling on its own) while the tree scrolls past. */
export const INSPECTOR_PANE = "min-w-0 @3xl:sticky @3xl:overflow-y-auto";

/**
 * Sticky offsets. The page scrolls the window: below `lg` the 64 px phone app bar sits on top, so
 * the bar sticks under it. The peek scrolls the sheet's own box, below the sheet's 8 px inset
 * (twice), border and 57 px header; scrolled-to elements clear the bar through the box's measured
 * scroll padding.
 */
export const OFFSETS = {
  page: {
    bar: "top-16 lg:top-0",
    inspector:
      "max-lg:@3xl:top-[128px] max-lg:@3xl:max-h-[calc(100dvh-136px)] lg:@3xl:top-16 lg:@3xl:max-h-[calc(100dvh-72px)]",
  },
  peek: { bar: "top-0", inspector: "@3xl:top-16 @3xl:max-h-[calc(100dvh-148px)]" },
} as const;
