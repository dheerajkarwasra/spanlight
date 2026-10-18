import { useId } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Rectangle,
  Tooltip,
  XAxis,
  YAxis,
  type BarShapeProps,
} from "recharts";

import { ChartTooltip } from "@/components/chart-parts";
import type { Bucket } from "@/lib/api";
import {
  BAR_CURSOR,
  BAR_MAX_SIZE,
  BAR_RADIUS,
  CHART_MARGIN,
  GRID_PROPS,
  PARTIAL_OPACITY,
  SERIES_COLORS,
  SURFACE_GAP_PROPS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";
import { formatCompact, formatInteger, formatPercent } from "@/lib/format";
import type { Band } from "@/lib/incident-bands";

import { PLOT_HEIGHT_CLASSES } from "./calls-errors-card";
import { formatBucketRange, formatBucketTick, type ChartPoint } from "./chart-data";
import { HatchPattern, IncidentBandAreas, IncidentKey } from "./incident-bands";
import { bandSpans, incidentsAt } from "./incident-overlay";

interface CallsErrorsPlotProps {
  points: ChartPoint[];
  bucket: Bucket;
  now: Date;
  /** The incidents to shade; empty when none overlap or the viewer switched them off. */
  bands: readonly Band[];
  /** The plot's accessible name: totals and, when shown, the incidents. */
  label: string;
}

/**
 * LLM calls per bucket on one axis: failed calls are a subset of the calls, so they stack in rose
 * on top of the succeeded calls in violet. Partial buckets are drawn at 45 %; provider incidents
 * are shaded bands behind the bars.
 */
export function CallsErrorsPlot({ points, bucket, now, bands, label }: CallsErrorsPlotProps) {
  const hatchId = `${useId()}-incident-hatch`;
  const byBucket = new Map(points.map((point) => [point.bucketStart, point]));

  /** Each segment fades with its bucket; the succeeded segment rounds its top when nothing failed. */
  function segment(props: BarShapeProps, top: boolean) {
    const point = points[props.originalDataIndex];
    return (
      <Rectangle
        {...props}
        radius={top || point?.errors === 0 ? BAR_RADIUS : 0}
        fillOpacity={point?.partial ? PARTIAL_OPACITY : 1}
      />
    );
  }

  return (
    <div role="img" aria-label={label} className={PLOT_HEIGHT_CLASSES}>
      <BarChart
        responsive
        style={{ width: "100%", height: "100%" }}
        data={points}
        margin={CHART_MARGIN}
        barCategoryGap="30%"
        accessibilityLayer={false}
      >
        <defs>
          <HatchPattern id={hatchId} />
        </defs>
        <CartesianGrid {...GRID_PROPS} />
        <XAxis
          dataKey="bucketStart"
          {...X_AXIS_PROPS}
          minTickGap={32}
          tickFormatter={(value: string) => formatBucketTick(value, bucket)}
        />
        <YAxis
          yAxisId="calls"
          {...Y_AXIS_PROPS}
          width="auto"
          tickCount={5}
          domain={[0, "auto"]}
          allowDecimals={false}
          tickFormatter={(value: number) => formatCompact(value) ?? ""}
        />
        <IncidentBandAreas
          spans={bandSpans(bands, points, bucket)}
          hatchId={hatchId}
          yAxisId="calls"
        />
        <Tooltip
          cursor={BAR_CURSOR}
          isAnimationActive={false}
          content={({ active, label: bucketStart }) => {
            const point = typeof bucketStart === "string" ? byBucket.get(bucketStart) : undefined;
            if (!point) {
              return null;
            }
            const rate = point.llmCalls > 0 ? point.errors / point.llmCalls : null;
            const errorValue = formatInteger(point.errors) ?? "0";
            return (
              <ChartTooltip
                active={active}
                title={formatBucketRange(point.bucketStart, bucket, now, point.partial)}
                rows={[
                  {
                    label: "Calls",
                    value: formatInteger(point.llmCalls),
                    color: SERIES_COLORS.calls,
                  },
                  {
                    label: "Failed",
                    value:
                      rate === null ? errorValue : `${errorValue} · ${formatPercent(rate) ?? ""}`,
                    color: SERIES_COLORS.errors,
                  },
                ]}
                footer={<IncidentLines titles={incidentsAt(bands, point.bucketStart, bucket)} />}
              />
            );
          }}
        />
        <Bar
          yAxisId="calls"
          dataKey="succeeded"
          name="Succeeded"
          stackId="calls"
          fill={SERIES_COLORS.calls}
          maxBarSize={BAR_MAX_SIZE}
          isAnimationActive={false}
          shape={(props: BarShapeProps) => segment(props, false)}
        />
        <Bar
          yAxisId="calls"
          dataKey="errors"
          name="Failed"
          stackId="calls"
          fill={SERIES_COLORS.errors}
          {...SURFACE_GAP_PROPS}
          maxBarSize={BAR_MAX_SIZE}
          isAnimationActive={false}
          shape={(props: BarShapeProps) => segment(props, true)}
        />
      </BarChart>
    </div>
  );
}

/** The tooltip's incident block: the overlapping incidents' titles, text only. */
function IncidentLines({ titles }: { titles: string[] }) {
  if (titles.length === 0) {
    return null;
  }
  return (
    <div className="mt-2 flex max-w-60 flex-col gap-1">
      <p className="flex items-center gap-1.5 text-rail-muted-foreground">
        <IncidentKey />
        {titles.length === 1 ? "Provider incident" : "Provider incidents"}
      </p>
      {titles.map((title) => (
        <p key={title} className="font-medium">
          {title}
        </p>
      ))}
    </div>
  );
}
