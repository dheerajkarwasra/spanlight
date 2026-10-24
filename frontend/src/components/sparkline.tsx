import { cn } from "@/lib/utils";

import { buildSparkline, SPARKLINE_BOX } from "./sparkline-path";

export type SparklineTone = "calls" | "errors" | "latency" | "cost" | "on-accent";

const TONE_COLORS: Record<SparklineTone, string> = {
  calls: "var(--chart-1)",
  errors: "var(--chart-2)",
  latency: "var(--chart-3)",
  cost: "var(--chart-4)",
  "on-accent": "var(--accent-card-foreground)",
};

interface SparklineProps {
  values: readonly (number | null)[];
  tone: SparklineTone;
  /** Put 0 on the bottom edge (counts and rates); latency keeps min–max. */
  zeroBased?: boolean;
  className?: string;
}

/**
 * The trend line in a KPI card (Figma "Sparkline/*"): a 2px rounded stroke with a dot on the
 * latest value, stretched to the card's width. The fixed viewBox scales without
 * preserving its aspect; `non-scaling-stroke` keeps the stroke 2px and the end dot (a
 * zero-length path with round caps) round. Decorative: the card's value and delta pill carry
 * the meaning, so it is hidden from assistive technology. With no data it falls back to the
 * dashed "empty" line.
 */
export function Sparkline({ values, tone, zeroBased = false, className }: SparklineProps) {
  const { paths, end } = buildSparkline(values, SPARKLINE_BOX, { zeroBased });
  const color = TONE_COLORS[tone];
  const { width, height } = SPARKLINE_BOX;

  if (paths.length === 0 && end === null) {
    return <EmptySparkline className={className} />;
  }

  return (
    <svg
      aria-hidden
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      fill="none"
      className={cn("block overflow-visible", className)}
    >
      {paths.map((path, index) => (
        <path
          key={index}
          d={path}
          stroke={color}
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {end ? (
        <path
          d={`M${end.x} ${end.y}h0.01`}
          stroke={color}
          strokeWidth={6}
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </svg>
  );
}

/** Figma "Sparkline/Empty": a dashed baseline for KPIs with no data yet. */
export function EmptySparkline({ className }: { className?: string }) {
  const { width, height } = SPARKLINE_BOX;
  return (
    <svg
      aria-hidden
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      fill="none"
      className={cn("block", className)}
    >
      <path
        d={`M3 ${height / 2}H${width - 3}`}
        stroke="var(--border)"
        strokeWidth={2}
        strokeLinecap="round"
        strokeDasharray="3 5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
