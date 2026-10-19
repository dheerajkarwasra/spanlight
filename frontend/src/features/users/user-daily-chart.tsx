import type { ReactNode } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, Tooltip, XAxis, YAxis } from "recharts";

import { ChartDataTableView } from "@/components/chart-data-table";
import { ChartTooltip, SeriesKey } from "@/components/chart-parts";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  BAR_CURSOR,
  BAR_MAX_SIZE,
  BAR_RADIUS,
  CHART_MARGIN,
  GRID_PROPS,
  LINE_SERIES_PROPS,
  SERIES_COLORS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";
import { formatCompact, formatCost, formatInteger } from "@/lib/format";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import { hasDailyCalls, type DailyPoint } from "./user-format";

const PLOT_HEIGHT_CLASSES = "h-[168px] sm:h-[220px]";
/** The cost line uses the warning token (5.6:1 on the card); `chart-4` is too light for a line. */
const COST_COLOR = "var(--warning)";

function costCell(point: DailyPoint): string {
  if (point.costText === null) {
    return point.calls > 0 ? "No price" : "—";
  }
  return point.lowerBound ? `${point.costText} (lower bound)` : point.costText;
}

function costAxisTick(value: number): string {
  // Ticks at $1k+ drop the cents so "$1,000.00" does not clip the 56 px axis.
  return (Math.abs(value) >= 1000 ? `$${formatCompact(value)}` : formatCost(value)) ?? "";
}

/**
 * LLM calls per UTC day as bars and cost as a line on its own right-hand axis. Days without
 * traces are zero; a gap in the line means the day had calls but no known price. Phones drop
 * the y axes. A hidden table gives screen readers the same numbers.
 */
export function UserDailyChart({ points }: { points: DailyPoint[] }) {
  const byDay = new Map(points.map((point) => [point.day, point]));
  const showAxes = useMediaQuery("(min-width: 640px)");
  const totalCalls = points.reduce((sum, point) => sum + point.calls, 0);

  return (
    <DailyCard>
      {hasDailyCalls(points) ? (
        <>
          <div
            role="img"
            aria-label={`LLM calls and cost per UTC day: ${formatInteger(totalCalls) ?? "0"} calls over ${points.length} days`}
            className={PLOT_HEIGHT_CLASSES}
          >
            <ComposedChart
              responsive
              style={{ width: "100%", height: "100%" }}
              data={points}
              margin={showAxes ? CHART_MARGIN : { top: 8, right: 4, bottom: 0, left: 4 }}
              accessibilityLayer={false}
            >
              <CartesianGrid {...GRID_PROPS} />
              <XAxis
                dataKey="day"
                {...X_AXIS_PROPS}
                tickFormatter={(day: string) => byDay.get(day)?.label ?? day}
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
                yAxisId="cost"
                orientation="right"
                hide={!showAxes}
                {...Y_AXIS_PROPS}
                width={56}
                tickFormatter={costAxisTick}
              />
              <Tooltip
                cursor={BAR_CURSOR}
                isAnimationActive={false}
                content={({ active, label }) => {
                  const point = typeof label === "string" ? byDay.get(label) : undefined;
                  if (!point) {
                    return null;
                  }
                  const unpriced = point.cost === null && point.calls > 0;
                  return (
                    <ChartTooltip
                      active={active}
                      title={`${point.tooltipLabel} (UTC)`}
                      rows={[
                        {
                          label: "Calls",
                          value: formatInteger(point.calls),
                          color: SERIES_COLORS.calls,
                        },
                        {
                          label: "Errors",
                          value: formatInteger(point.errors),
                          color: SERIES_COLORS.errors,
                        },
                        {
                          label: unpriced
                            ? "Cost (no price)"
                            : point.lowerBound
                              ? "Cost (lower bound)"
                              : "Cost",
                          value: point.costText,
                          color: COST_COLOR,
                        },
                      ]}
                    />
                  );
                }}
              />
              <Bar
                yAxisId="calls"
                dataKey="calls"
                name="LLM calls"
                fill={SERIES_COLORS.calls}
                radius={BAR_RADIUS}
                maxBarSize={BAR_MAX_SIZE}
                isAnimationActive={false}
              />
              <Line
                yAxisId="cost"
                dataKey="cost"
                name="Cost"
                {...LINE_SERIES_PROPS}
                stroke={COST_COLOR}
                dot={{ r: 3, fill: COST_COLOR, strokeWidth: 0 }}
                activeDot={{ ...LINE_SERIES_PROPS.activeDot, fill: COST_COLOR }}
                connectNulls={false}
              />
            </ComposedChart>
          </div>
          <ChartDataTableView
            table={{
              caption: "LLM calls, errors and cost per UTC day",
              columns: ["Day (UTC)", "LLM calls", "Errors", "Cost"],
              rows: points.map((point) => ({
                key: point.day,
                cells: [
                  point.label,
                  formatInteger(point.calls) ?? "0",
                  formatInteger(point.errors) ?? "0",
                  costCell(point),
                ],
              })),
            }}
          />
        </>
      ) : (
        <div
          className={cn(
            PLOT_HEIGHT_CLASSES,
            "flex items-center justify-center rounded-tile border border-dashed border-border text-sm text-muted-foreground",
          )}
        >
          No LLM calls in this window
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Days without traces show zero. A gap in the cost line means that day had calls but no known
        price.
      </p>
    </DailyCard>
  );
}

function DailyCard({ children }: { children: ReactNode }) {
  return (
    <Card className="flex min-w-0 flex-col gap-3.5 p-5 sm:gap-4 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-col gap-0.5">
          <CardTitle>Daily activity</CardTitle>
          <CardDescription className="mt-0 font-medium">
            LLM calls (bars) and cost (line) per UTC day
          </CardDescription>
        </div>
        <div aria-hidden className="flex shrink-0 items-center gap-4 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <SeriesKey color={SERIES_COLORS.calls} shape="bar" /> Calls
          </span>
          <span className="inline-flex items-center gap-1.5">
            <SeriesKey color={COST_COLOR} shape="line" /> Cost
          </span>
        </div>
      </div>
      {children}
    </Card>
  );
}

export function UserDailyChartSkeleton() {
  return (
    <Card aria-hidden className="flex flex-col gap-4 p-5 sm:p-6">
      <Skeleton className="h-5 w-36" />
      <Skeleton className={cn(PLOT_HEIGHT_CLASSES, "w-full rounded-tile")} />
    </Card>
  );
}
