import type { ReactNode } from "react";

import { AREA_FILL_OPACITY } from "@/lib/chart-theme";

export interface ChartTooltipRow {
  label: string;
  /** Formatted value; null renders as "—" (unknown for this bucket). */
  value: string | null;
  color: string;
}

interface ChartTooltipProps {
  active: boolean | undefined;
  title: string;
  rows: ChartTooltipRow[];
  /** Extra lines under the rows, e.g. the provider incidents overlapping the bucket. */
  footer?: ReactNode;
}

/**
 * Tooltip body for Recharts' `content` prop: an ink card (dark in both themes) where values
 * lead, series names follow, and each row is keyed with a dot in the series colour.
 */
export function ChartTooltip({ active, title, rows, footer }: ChartTooltipProps) {
  if (!active || rows.length === 0) {
    return null;
  }
  return (
    <div className="min-w-40 rounded-lg bg-hero-card px-3 py-2.5 text-xs text-hero-card-foreground shadow-lg dark:border dark:border-border">
      <p className="mb-2 text-xs text-rail-muted-foreground">{title}</p>
      <ul className="flex flex-col gap-1.5">
        {rows.map((row) => (
          <li key={row.label} className="flex items-center gap-2">
            <SeriesKey color={row.color} shape="dot" />
            <span className="text-rail-muted-foreground">{row.label}</span>
            <span className="ml-auto pl-4 font-semibold tabular">{row.value ?? "—"}</span>
          </li>
        ))}
      </ul>
      {footer}
    </div>
  );
}

/** A small swatch mirroring the mark: a block for bars, a stroke for lines, a dot for points. */
export function SeriesKey({ color, shape }: { color: string; shape: "bar" | "line" | "dot" }) {
  if (shape === "line") {
    return (
      <span
        aria-hidden
        className="inline-block h-[3px] w-3 shrink-0 rounded-full"
        style={{ backgroundColor: color }}
      />
    );
  }
  return (
    <span
      aria-hidden
      className={
        shape === "dot"
          ? "inline-block size-2 shrink-0 rounded-full"
          : "inline-block size-2.5 shrink-0 rounded-[3px]"
      }
      style={{ backgroundColor: color }}
    />
  );
}

/**
 * Vertical gradient for the fill under a line. Render inside the chart's `<defs>` and use
 * `fill={`url(#${id})`}` on the `<Area>`.
 */
export function AreaGradient({ id, color }: { id: string; color: string }) {
  return (
    <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stopColor={color} stopOpacity={AREA_FILL_OPACITY.top} />
      <stop offset="100%" stopColor={color} stopOpacity={AREA_FILL_OPACITY.bottom} />
    </linearGradient>
  );
}
