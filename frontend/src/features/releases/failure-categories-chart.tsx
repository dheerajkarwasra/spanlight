import { Bar, BarChart, CartesianGrid, ErrorBar, Tooltip, XAxis, YAxis } from "recharts";

import { ChartTooltip, SeriesKey } from "@/components/chart-parts";
import {
  BAR_CURSOR,
  BAR_MAX_SIZE,
  CHART_MARGIN,
  GRID_PROPS,
  SERIES_COLORS,
  SURFACE_GAP_PROPS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";
import { formatInteger, formatPercent } from "@/lib/format";

import { intervalText, type FailureCategoryPoint } from "./failure-categories";

/**
 * The release colours in fixed order, newest first (Figma: calls, latency, cost; never rose); one
 * per `CHART_RELEASE_LIMIT` release.
 */
const RELEASE_COLORS = [SERIES_COLORS.calls, SERIES_COLORS.latency, SERIES_COLORS.cost] as const;

/** Only the end that carries the value rounds (dataviz mark spec: 4px). */
const BAR_END_RADIUS: [number, number, number, number] = [4, 4, 0, 0];

interface Row {
  label: string;
  /** One point per charted release, in `releases` order. */
  points: (FailureCategoryPoint | undefined)[];
}

interface FailureCategoriesChartProps {
  /** The charted releases, newest first; each has reviewed traces. */
  releases: string[];
  categories: string[];
  points: FailureCategoryPoint[];
  /** Read out instead of the bars. */
  summary: string;
}

function colorOf(index: number): string {
  return RELEASE_COLORS[index] ?? SERIES_COLORS.calls;
}

/**
 * Figma "Failure categories card / chart": per category, a bar per release (oldest to newest, left
 * to right) for the share of reviewed traces, with the Wilson 95% interval as a whisker.
 */
export function FailureCategoriesChart({
  releases,
  categories,
  points,
  summary,
}: FailureCategoriesChartProps) {
  const rows: Row[] = categories.map((label) => ({
    label,
    points: releases.map((release) =>
      points.find((point) => point.label === label && point.release === release),
    ),
  }));
  const byLabel = new Map(rows.map((row) => [row.label, row]));
  // Oldest first, so each group reads left to right in time; colours stay with the release.
  const order = releases.map((release, index) => ({ release, index })).reverse();

  return (
    <div className="flex flex-col gap-4">
      <ul aria-label="Releases" className="flex flex-wrap gap-x-4 gap-y-2">
        {order.map(({ release, index }) => (
          <li key={release} className="flex items-center gap-1.5 text-xs font-medium">
            <SeriesKey color={colorOf(index)} shape="bar" />
            <span className="text-foreground">{release}</span>
          </li>
        ))}
      </ul>
      <div role="img" aria-label={summary} className="h-[260px]">
        <BarChart
          responsive
          style={{ width: "100%", height: "100%" }}
          data={rows}
          margin={CHART_MARGIN}
          barCategoryGap="20%"
          barGap={2}
          accessibilityLayer={false}
        >
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="label" {...X_AXIS_PROPS} minTickGap={8} interval="preserveStartEnd" />
          <YAxis
            {...Y_AXIS_PROPS}
            width={44}
            domain={[0, "auto"]}
            tickFormatter={(value: number) => formatPercent(value)?.replace(".0%", "%") ?? ""}
          />
          <Tooltip
            cursor={BAR_CURSOR}
            isAnimationActive={false}
            content={({ active, label }) => {
              const row = typeof label === "string" ? byLabel.get(label) : undefined;
              if (!row) {
                return null;
              }
              return (
                <ChartTooltip
                  active={active}
                  title={row.label}
                  rows={order.map(({ release, index }) => ({
                    label: release,
                    value: tooltipValue(row.points[index]),
                    color: colorOf(index),
                  }))}
                />
              );
            }}
          />
          {order.map(({ release, index }) => (
            <Bar
              key={release}
              dataKey={(row: Row) => row.points[index]?.rate ?? null}
              name={release}
              fill={colorOf(index)}
              {...SURFACE_GAP_PROPS}
              radius={BAR_END_RADIUS}
              maxBarSize={BAR_MAX_SIZE}
              isAnimationActive={false}
            >
              <ErrorBar
                dataKey={(row: Row) => whisker(row.points[index])}
                width={4}
                strokeWidth={1.5}
                stroke="var(--foreground)"
                isAnimationActive={false}
              />
            </Bar>
          ))}
        </BarChart>
      </div>
    </div>
  );
}

/** The interval as distances below and above the rate, as `ErrorBar` takes them. */
function whisker(point: FailureCategoryPoint | undefined): [number, number] | undefined {
  if (!point || point.rate === null || point.low === null || point.high === null) {
    return undefined;
  }
  return [point.rate - point.low, point.high - point.rate];
}

function tooltipValue(point: FailureCategoryPoint | undefined): string | null {
  if (!point || point.rate === null) {
    return null;
  }
  return `${formatPercent(point.rate) ?? "—"} (${intervalText(point)}) · ${formatInteger(point.count) ?? "0"} of ${formatInteger(point.reviewed) ?? "0"}`;
}
