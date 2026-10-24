import { CartesianGrid, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";

import {
  CHART_MARGIN,
  GRID_PROPS,
  LINE_CURSOR,
  LINE_SERIES_PROPS,
  SERIES_COLORS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";

import { tickPositions, shortUsd } from "./budget-ticks";
import { burnTooltip, dayLabel, periodDays, type BurnPoint } from "./burn-series";
import type { BudgetBurn } from "@/lib/api";

const LINE_COLOR = SERIES_COLORS.cost;

interface BurnChartProps {
  burn: BudgetBurn;
  points: BurnPoint[];
  /** The plot in words for screen readers; the table view lists every day. */
  summary: string;
}

/**
 * Spec ruling 11: spend to date as one 2 px `--chart-4` line over every day of the period (it
 * stops at today), the amount as a dashed line and 50 / 80 % as dotted ones. One y axis, labelled
 * only at those marks ("$250"), each mark line labelled with its percent. No projection.
 */
export function BurnChart({ burn, points, summary }: BurnChartProps) {
  const amount = Number(burn.amount_usd);
  const byDay = new Map(points.map((point) => [point.day, point]));
  const data = periodDays(burn).map((day) => ({ day, cumulative: byDay.get(day)?.cumulative }));
  const peak = Math.max(amount, ...points.map((point) => point.cumulative ?? 0));
  const ticks = tickPositions(amount);

  return (
    <div role="img" aria-label={summary} className="h-[200px] w-full">
      <LineChart
        responsive
        style={{ width: "100%", height: "100%" }}
        data={data}
        margin={{ ...CHART_MARGIN, right: 12 }}
        accessibilityLayer={false}
      >
        <CartesianGrid {...GRID_PROPS} />
        <XAxis
          dataKey="day"
          tickFormatter={dayLabel}
          interval="preserveStartEnd"
          {...X_AXIS_PROPS}
        />
        <YAxis
          {...Y_AXIS_PROPS}
          width={80}
          domain={[0, peak]}
          ticks={[0, ...ticks.map((tick) => (amount * tick.pct) / 100)]}
          tickFormatter={(value: number) => shortUsd(value)}
        />
        {ticks.map((tick) => (
          <ReferenceLine
            key={tick.pct}
            y={(amount * tick.pct) / 100}
            stroke={tick.pct === 100 ? "var(--muted-foreground)" : "var(--border-strong)"}
            strokeDasharray={tick.pct === 100 ? "6 4" : "1 3"}
            ifOverflow="extendDomain"
            label={{
              value: `${tick.pct} %`,
              position: "insideTopRight",
              fontSize: 10,
              fill: "var(--muted-foreground)",
            }}
          />
        ))}
        <Tooltip
          cursor={LINE_CURSOR}
          isAnimationActive={false}
          content={({ active, label }) => {
            const point = typeof label === "string" ? byDay.get(label) : undefined;
            return active && point ? (
              <p className="rounded-lg bg-hero-card px-3 py-2 text-xs font-semibold text-hero-card-foreground tabular shadow-lg dark:border dark:border-border">
                {burnTooltip(point)}
              </p>
            ) : null;
          }}
        />
        <Line
          dataKey="cumulative"
          name="Spend to date"
          {...LINE_SERIES_PROPS}
          type="linear"
          strokeWidth={2}
          stroke={LINE_COLOR}
          // A one-day period has a single point: show it as a dot.
          dot={points.length === 1 ? { r: 3, fill: LINE_COLOR } : false}
          activeDot={{ ...LINE_SERIES_PROPS.activeDot, fill: LINE_COLOR }}
        />
      </LineChart>
    </div>
  );
}
