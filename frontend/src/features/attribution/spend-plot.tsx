import { Bar, BarChart, CartesianGrid, Rectangle, Tooltip, XAxis, YAxis } from "recharts";

import {
  BAR_CURSOR,
  BAR_MAX_SIZE,
  BAR_RADIUS,
  CHART_MARGIN,
  GRID_PROPS,
  SERIES_COLORS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";

import { barLines, spendTick, type SpendBar } from "./spend-series";

/** Spec §3.3: a 160 px plot, 120 px on phones. */
export const PLOT_CLASS = "h-[120px] md:h-[160px]";

interface SpendPlotProps {
  bars: SpendBar[];
  per: string;
  pendingTraces: number | null;
}

/** A bar whose bucket had unpriced calls gets a dotted top edge: the real spend is higher. */
function SpendBarShape(props: { x?: number; y?: number; width?: number; payload?: SpendBar }) {
  const { x = 0, y = 0, width = 0, payload } = props;
  return (
    <g>
      <Rectangle {...props} radius={BAR_RADIUS} />
      {payload?.lowerBound ? (
        <line
          x1={x}
          x2={x + width}
          y1={y - 3}
          y2={y - 3}
          stroke="var(--muted-foreground)"
          strokeWidth={2}
          strokeDasharray="2 2"
        />
      ) : null}
    </g>
  );
}

export function SpendPlot({ bars, per, pendingTraces }: SpendPlotProps) {
  const byStart = new Map(
    bars.map((bar, index) => [bar.start, { bar, last: index === bars.length - 1 }]),
  );
  const lowerBounds = bars.filter((bar) => bar.lowerBound).length;
  const summary = `Spend ${per}, ${String(bars.length)} bars${lowerBounds > 0 ? `, ${String(lowerBounds)} of them lower bounds` : ""}. Show the table for every value.`;
  return (
    <div role="img" aria-label={summary} className={PLOT_CLASS}>
      <BarChart
        responsive
        style={{ width: "100%", height: "100%" }}
        data={bars}
        margin={CHART_MARGIN}
        // Recharts pads each side of a slot by this share: bars fill half a slot, so neighbours
        // sit 2 px apart once a slot is 4 px (any desktop width; phones up to ~65 bars).
        barCategoryGap="25%"
        accessibilityLayer={false}
      >
        <CartesianGrid {...GRID_PROPS} />
        <XAxis
          dataKey="start"
          {...X_AXIS_PROPS}
          minTickGap={32}
          tickFormatter={(start: string) => byStart.get(start)?.bar.label ?? ""}
        />
        <YAxis {...Y_AXIS_PROPS} width="auto" tickCount={4} tickFormatter={spendTick} />
        <Tooltip
          cursor={BAR_CURSOR}
          isAnimationActive={false}
          content={({ active, label }) => {
            const hit = typeof label === "string" ? byStart.get(label) : undefined;
            return active && hit ? (
              <div className="flex flex-col gap-1 rounded-lg bg-hero-card px-3 py-2 text-xs text-hero-card-foreground tabular shadow-lg dark:border dark:border-border">
                {barLines(hit.bar, hit.last, pendingTraces).map((line, index) => (
                  <p
                    key={line}
                    className={index === 0 ? "font-semibold" : "text-rail-muted-foreground"}
                  >
                    {line}
                  </p>
                ))}
              </div>
            ) : null;
          }}
        />
        <Bar
          dataKey="cost"
          name="Spend"
          fill={SERIES_COLORS.cost}
          radius={BAR_RADIUS}
          maxBarSize={BAR_MAX_SIZE}
          shape={SpendBarShape}
          isAnimationActive={false}
        />
      </BarChart>
    </div>
  );
}
