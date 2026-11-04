import { Bar, BarChart, Rectangle, Tooltip, YAxis, type BarShapeProps } from "recharts";

import { ChartTooltip } from "@/components/chart-parts";
import { BAR_CURSOR, PARTIAL_OPACITY } from "@/lib/chart-theme";
import { cn } from "@/lib/utils";

export type SparklineTone = "calls" | "errors" | "latency" | "cost" | "on-accent";

const TONE_COLORS: Record<SparklineTone, string> = {
  calls: "var(--chart-1)",
  errors: "var(--chart-2)",
  latency: "var(--chart-3)",
  cost: "var(--chart-4)",
  "on-accent": "var(--accent-card-foreground)",
};

export interface SparklinePoint {
  /** The tooltip title: the bucket ("Nov 1, 14:00–15:00 · partial") or the turn. */
  label: string;
  /** null when the bucket has no data: it gets no column, and "—" in the tooltip. */
  value: number | null;
  /** The bucket is still filling or clipped by the window; drawn fainter. */
  partial?: boolean;
}

export interface SparklineProps {
  data: readonly SparklinePoint[];
  tone: SparklineTone;
  /** The value's name in the tooltip, e.g. "Calls". */
  name: string;
  format: (value: number) => string | null;
  className?: string;
}

/**
 * The trend in a KPI card (Figma "Sparkline/*"): one zero-based column per bucket, so a spike
 * reads as one bucket and an empty bucket stays empty instead of being bridged by a line. Partial
 * buckets fade like the Calls & errors chart; hovering a column shows its bucket and value. The
 * card's value and delta carry the meaning, so the plot is hidden from assistive technology.
 */
export function Sparkline({ data, tone, name, format, className }: SparklineProps) {
  if (!data.some((point) => point.value !== null)) {
    return <EmptySparkline className={className} />;
  }
  const color = TONE_COLORS[tone];

  return (
    <div aria-hidden className={cn("w-full", className)}>
      <BarChart
        responsive
        style={{ width: "100%", height: "100%" }}
        data={data}
        margin={{ top: 2, right: 0, bottom: 0, left: 0 }}
        barCategoryGap="20%"
        accessibilityLayer={false}
      >
        <YAxis hide domain={[0, "dataMax"]} />
        <Tooltip
          cursor={BAR_CURSOR}
          isAnimationActive={false}
          filterNull={false}
          allowEscapeViewBox={{ x: true, y: true }}
          wrapperStyle={{ zIndex: 20 }}
          content={({ active, payload }) => {
            const point = payload[0]?.payload as SparklinePoint | undefined;
            return point ? (
              <ChartTooltip
                active={active}
                title={point.label}
                rows={[
                  { label: name, value: point.value === null ? null : format(point.value), color },
                ]}
              />
            ) : null;
          }}
        />
        <Bar
          dataKey="value"
          fill={color}
          isAnimationActive={false}
          shape={(props: BarShapeProps) => (
            <Rectangle
              {...props}
              radius={[2, 2, 0, 0]}
              fillOpacity={data[props.originalDataIndex]?.partial ? PARTIAL_OPACITY : 1}
            />
          )}
        />
      </BarChart>
    </div>
  );
}

/** Figma "Sparkline/Empty": a dashed baseline for KPIs with no data yet. */
export function EmptySparkline({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      width="100%"
      height={32}
      viewBox="0 0 96 32"
      preserveAspectRatio="none"
      fill="none"
      className={cn("block", className)}
    >
      <path
        d="M3 16H93"
        stroke="var(--border)"
        strokeWidth={2}
        strokeLinecap="round"
        strokeDasharray="3 5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
