/**
 * Shared Recharts styling (Daylight Bento). Colours are CSS custom properties from
 * globals.css, so charts follow the light/dark theme without re-rendering.
 *
 * Conventions: smooth `monotone` lines with 2.5px round strokes, a violet gradient fill under
 * the main series (see `AreaGradient` in components/chart-parts), a solid hairline horizontal grid
 * only (dashed grids read as thresholds), bars with 4px rounded tops and the peak bar in lime, and
 * the ink `ChartTooltip`.
 */

export const CHART_HEIGHT = 180;

export const SERIES_COLORS = {
  calls: "var(--chart-1)",
  errors: "var(--chart-2)",
  latency: "var(--chart-3)",
  cost: "var(--chart-4)",
} as const;

export const CHART_MARGIN = { top: 8, right: 8, bottom: 0, left: 0 };

export const GRID_PROPS = {
  stroke: "var(--chart-grid)",
  strokeWidth: 1,
  vertical: false,
} as const;

const AXIS_TICK = { fill: "var(--muted-foreground)", fontSize: 12 } as const;

export const X_AXIS_PROPS = {
  tick: AXIS_TICK,
  tickLine: false,
  axisLine: false,
  minTickGap: 24,
  tickMargin: 8,
} as const;

export const Y_AXIS_PROPS = {
  tick: AXIS_TICK,
  tickLine: false,
  axisLine: false,
  width: 56,
  tickMargin: 6,
} as const;

/** Spread onto `<Line>` / `<Area>`: smooth curve, 2.5px round stroke, no dots until hover. */
export const LINE_SERIES_PROPS = {
  type: "monotone",
  strokeWidth: 2.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  dot: false,
  activeDot: { r: 4, strokeWidth: 2, stroke: "var(--surface)" },
  isAnimationActive: false,
} as const;

/** Gradient stops for the fill under the main series: 22% at the line, fading to 0. */
export const AREA_FILL_OPACITY = { top: 0.22, bottom: 0 } as const;

/** Bars stay slim and round only the end that carries the value. */
export const BAR_MAX_SIZE = 24;
export const BAR_RADIUS: [number, number, number, number] = [4, 4, 0, 0];

/** A bucket the window clips (the first) or that is still filling (the last) is drawn at 45 %. */
export const PARTIAL_OPACITY = 0.45;

/** The latest or peak bar is lime; on ink hero cards the other bars are translucent white. */
export const HIGHLIGHT_BAR_COLOR = "var(--lime)";
export const INK_BAR_COLOR = "var(--chart-bar-muted-on-ink)";
export const INK_AXIS_TICK = { fill: "var(--rail-subtle-foreground)", fontSize: 12 } as const;

/** A surface-coloured hairline separates stacked segments (the "surface gap"). */
export const SURFACE_GAP_PROPS = {
  stroke: "var(--surface)",
  strokeWidth: 1,
} as const;

/** Tooltip cursors: a soft column behind bars, a dashed rule for lines. */
export const BAR_CURSOR = { fill: "var(--surface-muted)" } as const;
export const LINE_CURSOR = { stroke: "var(--border)", strokeDasharray: "4 4" } as const;
