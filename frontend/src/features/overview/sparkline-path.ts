/**
 * Geometry for the KPI sparklines (Figma "Sparkline/*"): a 96×32 box, a 3px inset at the sides
 * and 4px at top and bottom so the round caps and the end dot are never clipped.
 */

export interface SparklineBox {
  width: number;
  height: number;
  insetX: number;
  insetY: number;
}

export const SPARKLINE_BOX: SparklineBox = { width: 96, height: 32, insetX: 3, insetY: 4 };

export interface SparklineGeometry {
  /** One SVG path per run of consecutive known values; unknown buckets break the line. */
  paths: string[];
  /** Where the end dot goes: the latest known value. */
  end: { x: number; y: number } | null;
}

interface Point {
  x: number;
  y: number;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/** A smooth curve through the points: each segment eases out horizontally, like the Figma art. */
function smoothPath(points: readonly Point[]): string {
  const [first, ...rest] = points;
  if (!first) {
    return "";
  }
  let path = `M${round(first.x)} ${round(first.y)}`;
  let previous = first;
  for (const point of rest) {
    const midX = (previous.x + point.x) / 2;
    path += ` C${round(midX)} ${round(previous.y)} ${round(midX)} ${round(point.y)} ${round(point.x)} ${round(point.y)}`;
    previous = point;
  }
  return path;
}

/**
 * Scales a series into the sparkline box. Higher values sit higher. `zeroBased` puts 0 on the
 * bottom edge (counts and rates), otherwise the domain is min–max and a flat series is drawn
 * through the middle. Runs of a single known value have no line but still get the end dot when
 * they are the latest value.
 */
export function buildSparkline(
  values: readonly (number | null)[],
  box: SparklineBox = SPARKLINE_BOX,
  { zeroBased = false }: { zeroBased?: boolean } = {},
): SparklineGeometry {
  const known = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (known.length === 0) {
    return { paths: [], end: null };
  }

  const min = zeroBased ? Math.min(0, ...known) : Math.min(...known);
  const max = Math.max(...known);
  const plotWidth = box.width - box.insetX * 2;
  const plotHeight = box.height - box.insetY * 2;
  const stepX = values.length > 1 ? plotWidth / (values.length - 1) : 0;

  function toPoint(value: number, index: number): Point {
    const x = values.length > 1 ? box.insetX + index * stepX : box.width - box.insetX;
    let ratio = zeroBased ? 0 : 0.5;
    if (max !== min) {
      ratio = (value - min) / (max - min);
    }
    return { x, y: box.insetY + (1 - ratio) * plotHeight };
  }

  const runs: Point[][] = [];
  let currentRun: Point[] = [];
  let end: Point | null = null;
  for (const [index, value] of values.entries()) {
    if (value === null || !Number.isFinite(value)) {
      if (currentRun.length > 0) {
        runs.push(currentRun);
        currentRun = [];
      }
      continue;
    }
    const point = toPoint(value, index);
    currentRun.push(point);
    end = point;
  }
  if (currentRun.length > 0) {
    runs.push(currentRun);
  }

  return {
    paths: runs.filter((run) => run.length > 1).map(smoothPath),
    end,
  };
}
