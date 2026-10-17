import { useId } from "react";
import { Area, CartesianGrid, ComposedChart, Line, Tooltip, XAxis, YAxis } from "recharts";

import { AreaGradient, ChartTooltip } from "@/components/chart-parts";
import type { Bucket } from "@/lib/api";
import {
  CHART_MARGIN,
  GRID_PROPS,
  LINE_CURSOR,
  LINE_SERIES_PROPS,
  SERIES_COLORS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";
import { formatCompact, formatInteger, formatPercent } from "@/lib/format";
import type { Band } from "@/lib/incident-bands";
import { useMediaQuery } from "@/lib/use-media-query";

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
 * LLM calls per bucket as a violet area, failed calls as a rose line on their own right-hand
 * axis (errors are usually two orders of magnitude smaller), provider incidents as shaded bands
 * behind both. Phones get a simplified plot without y axes.
 */
export function CallsErrorsPlot({ points, bucket, now, bands, label }: CallsErrorsPlotProps) {
  const ids = useId();
  const gradientId = `${ids}-calls-fill`;
  const hatchId = `${ids}-incident-hatch`;
  const byBucket = new Map(points.map((point) => [point.bucketStart, point]));
  const showAxes = useMediaQuery("(min-width: 640px)");

  return (
    <div role="img" aria-label={label} className={PLOT_HEIGHT_CLASSES}>
      <ComposedChart
        responsive
        style={{ width: "100%", height: "100%" }}
        data={points}
        margin={showAxes ? CHART_MARGIN : { top: 8, right: 4, bottom: 0, left: 4 }}
        accessibilityLayer={false}
      >
        <defs>
          <AreaGradient id={gradientId} color={SERIES_COLORS.calls} />
          <HatchPattern id={hatchId} />
        </defs>
        <CartesianGrid {...GRID_PROPS} />
        <XAxis
          dataKey="bucketStart"
          {...X_AXIS_PROPS}
          minTickGap={showAxes ? 32 : 48}
          tickFormatter={(value: string) => formatBucketTick(value, bucket)}
        />
        <YAxis
          yAxisId="calls"
          hide={!showAxes}
          {...Y_AXIS_PROPS}
          width={40}
          allowDecimals={false}
          tickFormatter={(value: number) => formatCompact(value) ?? ""}
        />
        <YAxis
          yAxisId="errors"
          orientation="right"
          hide={!showAxes}
          {...Y_AXIS_PROPS}
          tick={{ fill: "var(--danger-text)", fontSize: 12 }}
          width={32}
          allowDecimals={false}
          tickFormatter={(value: number) => formatCompact(value) ?? ""}
        />
        <IncidentBandAreas
          spans={bandSpans(bands, points, bucket)}
          hatchId={hatchId}
          yAxisId="calls"
        />
        <Tooltip
          cursor={LINE_CURSOR}
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
                title={formatBucketRange(point.bucketStart, bucket, now)}
                rows={[
                  {
                    label: "Calls",
                    value: formatInteger(point.llmCalls),
                    color: SERIES_COLORS.calls,
                  },
                  {
                    label: "Errors",
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
        <Area
          yAxisId="calls"
          dataKey="llmCalls"
          name="LLM calls"
          {...LINE_SERIES_PROPS}
          stroke={SERIES_COLORS.calls}
          fill={`url(#${gradientId})`}
          activeDot={{ ...LINE_SERIES_PROPS.activeDot, fill: SERIES_COLORS.calls }}
        />
        <Line
          yAxisId="errors"
          dataKey="errors"
          name="Errors"
          {...LINE_SERIES_PROPS}
          stroke={SERIES_COLORS.errors}
          activeDot={{ ...LINE_SERIES_PROPS.activeDot, fill: SERIES_COLORS.errors }}
        />
      </ComposedChart>
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
