import { Bar, BarChart, BarStack, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";

import { ChartTooltip } from "@/components/chart-parts";
import type { Bucket, ErrorClass } from "@/lib/api";
import {
  BAR_CURSOR,
  BAR_MAX_SIZE,
  CHART_MARGIN,
  GRID_PROPS,
  SURFACE_GAP_PROPS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";
import { errorClassColor, errorClassLabel } from "@/lib/error-class";
import { formatCompact, formatInteger } from "@/lib/format";

import { bucketRange, bucketTick } from "./bucket-labels";
import type { StackedPoint } from "./error-series";

interface ErrorClassPlotProps {
  points: StackedPoint[];
  classes: ErrorClass[];
  bucket: Bucket;
  /** Read out instead of the bars: the window's totals per class. */
  summary: string;
}

/** Only the outer corners of a whole stack round, as one bar would (dataviz mark spec: 4px). */
const STACK_RADIUS: [number, number, number, number] = [4, 4, 0, 0];

/**
 * Failed calls per bucket stacked by class in the fixed order and colours, with a per-bar tooltip
 * that lists the bucket's non-zero classes and their total.
 */
export function ErrorClassPlot({ points, classes, bucket, summary }: ErrorClassPlotProps) {
  const byBucket = new Map(points.map((point) => [point.bucketStart, point]));

  return (
    <div role="img" aria-label={summary} className="h-[200px] sm:h-[244px]">
      <BarChart
        responsive
        style={{ width: "100%", height: "100%" }}
        data={points}
        margin={CHART_MARGIN}
        barCategoryGap="22%"
        accessibilityLayer={false}
      >
        <CartesianGrid {...GRID_PROPS} />
        <XAxis
          dataKey="bucketStart"
          {...X_AXIS_PROPS}
          minTickGap={32}
          tickFormatter={(value: string) => bucketTick(value, bucket)}
        />
        <YAxis
          {...Y_AXIS_PROPS}
          width={32}
          allowDecimals={false}
          tickFormatter={(value: number) => formatCompact(value) ?? ""}
        />
        <Tooltip
          cursor={BAR_CURSOR}
          isAnimationActive={false}
          content={({ active, label }) => {
            const point = typeof label === "string" ? byBucket.get(label) : undefined;
            if (!point || point.total === 0) {
              return null;
            }
            return (
              <ChartTooltip
                active={active}
                title={bucketRange(point.bucketStart, bucket)}
                rows={[
                  ...classes
                    .filter((name) => (point.counts[name] ?? 0) > 0)
                    .map((name) => ({
                      label: errorClassLabel(name),
                      value: formatInteger(point.counts[name]),
                      color: errorClassColor(name),
                    })),
                  { label: "Total", value: formatInteger(point.total), color: "transparent" },
                ]}
              />
            );
          }}
        />
        <BarStack radius={STACK_RADIUS}>
          {classes.map((name) => (
            <Bar
              key={name}
              dataKey={(point: StackedPoint) => point.counts[name] ?? 0}
              name={errorClassLabel(name)}
              fill={errorClassColor(name)}
              {...SURFACE_GAP_PROPS}
              maxBarSize={BAR_MAX_SIZE}
              isAnimationActive={false}
            />
          ))}
        </BarStack>
      </BarChart>
    </div>
  );
}
