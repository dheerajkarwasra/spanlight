import { useNavigate } from "@tanstack/react-router";
import { useId, useRef } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Rectangle,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
  type BarShapeProps,
} from "recharts";

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
import { formatCompact } from "@/lib/format";
import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import type { Band } from "@/lib/incident-bands";

import { bucketSearch } from "./bucket-link";
import { PLOT_HEIGHT_CLASSES } from "./calls-errors-card";
import { BucketTooltip } from "./calls-errors-tooltip";
import { formatBucketTick, type ChartPoint } from "./chart-data";
import { HatchPattern, IncidentBandAreas } from "./incident-bands";
import { bandSpans } from "./incident-overlay";
import type { MarkerLine } from "./release-markers";

interface CallsErrorsPlotProps {
  points: ChartPoint[];
  bucket: Bucket;
  now: Date;
  /** The incidents to shade; empty when none overlap or the viewer switched them off. */
  bands: readonly Band[];
  /** Dashed release lines, one per bucket a release was first seen in. */
  releaseLines: readonly MarkerLine[];
  /** The plot's accessible name: totals and, when shown, the incidents. */
  label: string;
}

/**
 * LLM calls per bucket on one axis: failed calls are a subset of the calls, so they stack in rose
 * on top of the succeeded calls in violet. Partial buckets are drawn at 45 %; provider incidents
 * are shaded bands behind the bars and releases dashed lines. Clicking a bucket (anywhere in its
 * column, so narrow hour bars still have a full-height target) opens its traces; clicking the
 * failed segment opens only the failed ones. The table view is the keyboard path.
 */
export function CallsErrorsPlot({
  points,
  bucket,
  now,
  bands,
  releaseLines,
  label,
}: CallsErrorsPlotProps) {
  const hatchId = `${useId()}-incident-hatch`;
  const byBucket = new Map(points.map((point) => [point.bucketStart, point]));
  const navigate = useNavigate();
  const { orgId, projectId } = useProjectParams();
  const { environment } = useProjectFilters();
  // The failed segment's click runs before the chart's (it bubbles up), so it marks the click.
  const failedClicked = useRef(false);

  function openBucket(bucketStart: unknown) {
    const errorsOnly = failedClicked.current;
    failedClicked.current = false;
    if (typeof bucketStart === "string" && byBucket.has(bucketStart)) {
      void navigate({
        to: "/$orgId/$projectId/traces",
        params: { orgId, projectId },
        search: bucketSearch(bucketStart, bucket, environment, errorsOnly),
      });
    }
  }

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
        style={{ width: "100%", height: "100%", cursor: "pointer" }}
        data={points}
        margin={CHART_MARGIN}
        barCategoryGap="30%"
        accessibilityLayer={false}
        onClick={(state) => {
          openBucket(state.activeLabel);
        }}
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
        {releaseLines.map((line) => (
          <ReferenceLine
            key={line.bucketStart}
            yAxisId="calls"
            x={line.bucketStart}
            stroke="var(--border-strong)"
            strokeDasharray="4 4"
            label={{
              value: line.label,
              position: "insideTopLeft",
              fill: "var(--muted-foreground)",
              fontSize: 11,
            }}
          />
        ))}
        <Tooltip
          cursor={BAR_CURSOR}
          isAnimationActive={false}
          content={({ active, label: bucketStart }) => {
            const point = typeof bucketStart === "string" ? byBucket.get(bucketStart) : undefined;
            return point ? (
              <BucketTooltip
                active={active}
                point={point}
                bucket={bucket}
                now={now}
                bands={bands}
              />
            ) : null;
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
          // 1 px of the stroke falls on each side of the edge: a 2 px gap (spec ruling 16).
          strokeWidth={2}
          maxBarSize={BAR_MAX_SIZE}
          isAnimationActive={false}
          onClick={() => {
            failedClicked.current = true;
          }}
          shape={(props: BarShapeProps) => segment(props, true)}
        />
      </BarChart>
    </div>
  );
}
