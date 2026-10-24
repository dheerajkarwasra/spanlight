import { useId } from "react";
import { Bar, BarChart, BarStack, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";

import { SeriesKey } from "@/components/chart-parts";
import { ChartViewToggle } from "@/components/chart-view-toggle";
import { Card, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableRowHeader,
} from "@/components/ui/table";
import type { GatewaySeries } from "@/lib/api";
import {
  BAR_CURSOR,
  BAR_MAX_SIZE,
  BAR_RADIUS,
  CHART_MARGIN,
  GRID_PROPS,
  SURFACE_GAP_PROPS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/lib/chart-theme";
import { formatCompact, formatInteger, pluralize } from "@/lib/format";
import type { RangeValue } from "@/lib/time-range";
import { useChartView } from "@/lib/use-stored-choice";

import { windowPhrase } from "./overview-format";
import { barSummary, requestsBars, type RequestsBar } from "./requests-series";

/** Per viewer, per browser: the card opens the way it was left. */
const VIEW_KEY = "spanlight-gateway-requests-view";

const OK_COLOR = "var(--chart-1)";
const ERRORS_COLOR = "var(--danger)";

/**
 * "Requests over time" (spec ruling 21): requests per bucket with the errors stacked on top. The
 * bars don't link, so they take no focus: pointers get the tooltip, keyboards the table view.
 */
export function RequestsChart({ series, window }: { series: GatewaySeries; window: RangeValue }) {
  const titleId = useId();
  const [view, setView] = useChartView(VIEW_KEY);
  const bars = requestsBars(series);
  const per = series.bucket === "hour" ? "hour" : "5 minutes";
  const errors = bars.reduce((sum, bar) => sum + bar.errors, 0);
  const requests = bars.reduce((sum, bar) => sum + bar.ok, errors);
  const summary = `Requests per ${per}, ${windowPhrase(window)}: ${pluralize(requests, "request")}, ${pluralize(errors, "error")}`;

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="flex min-w-0 basis-full flex-col gap-1 sm:flex-1 sm:basis-auto">
          <CardTitle id={titleId}>Requests over time</CardTitle>
          <p className="text-xs font-medium text-muted-foreground">
            Per {per}, {windowPhrase(window)} · errors stacked on top
          </p>
        </div>
        <ul aria-label="Legend" className="flex items-center gap-4 text-xs font-medium">
          <li className="flex items-center gap-1.5">
            <SeriesKey color={OK_COLOR} shape="bar" />
            Succeeded
          </li>
          <li className="flex items-center gap-1.5">
            <SeriesKey color={ERRORS_COLOR} shape="bar" />
            Errors
          </li>
        </ul>
        <ChartViewToggle view={view} onChange={setView} />
      </div>
      {view === "table" ? (
        <RequestsTable bars={bars} per={per} />
      ) : (
        <RequestsPlot bars={bars} summary={summary} />
      )}
    </Card>
  );
}

function RequestsPlot({ bars, summary }: { bars: RequestsBar[]; summary: string }) {
  const byStart = new Map(bars.map((bar) => [bar.start, bar]));
  return (
    <div role="img" aria-label={summary} className="h-[140px]">
      <BarChart
        responsive
        style={{ width: "100%", height: "100%" }}
        data={bars}
        margin={CHART_MARGIN}
        barCategoryGap="30%"
        accessibilityLayer={false}
      >
        <CartesianGrid {...GRID_PROPS} />
        <XAxis
          dataKey="start"
          {...X_AXIS_PROPS}
          minTickGap={32}
          tickFormatter={(start: string) => byStart.get(start)?.label ?? ""}
        />
        <YAxis
          {...Y_AXIS_PROPS}
          width="auto"
          tickCount={4}
          allowDecimals={false}
          tickFormatter={(value: number) => formatCompact(value) ?? ""}
        />
        <Tooltip
          cursor={BAR_CURSOR}
          isAnimationActive={false}
          content={({ active, label }) => {
            const bar = typeof label === "string" ? byStart.get(label) : undefined;
            return active && bar ? (
              <p className="rounded-lg bg-hero-card px-3 py-2 text-xs font-semibold text-hero-card-foreground tabular shadow-lg dark:border dark:border-border">
                {barSummary(bar)}
              </p>
            ) : null;
          }}
        />
        <BarStack radius={BAR_RADIUS}>
          <Bar
            dataKey="ok"
            name="Succeeded"
            fill={OK_COLOR}
            maxBarSize={BAR_MAX_SIZE}
            isAnimationActive={false}
          />
          <Bar
            dataKey="errors"
            name="Errors"
            fill={ERRORS_COLOR}
            {...SURFACE_GAP_PROPS}
            // 1 px of the stroke falls on each side of the edge: a 2 px gap.
            strokeWidth={2}
            maxBarSize={BAR_MAX_SIZE}
            isAnimationActive={false}
          />
        </BarStack>
      </BarChart>
    </div>
  );
}

/** The "Show table" view: every bucket with its requests and errors, the keyboard path. */
function RequestsTable({ bars, per }: { bars: RequestsBar[]; per: string }) {
  return (
    <div
      role="region"
      aria-label={`Requests per ${per}, table`}
      tabIndex={0}
      className="max-h-[420px] overflow-auto rounded-tile border border-border"
    >
      <Table className="text-xs" containerClassName="overflow-visible">
        <caption className="sr-only">Requests and errors per {per}</caption>
        <TableHeader className="sticky top-0 bg-surface">
          <TableRow>
            <TableHead scope="col">Start</TableHead>
            <TableHead scope="col" className="text-right">
              Requests
            </TableHead>
            <TableHead scope="col" className="text-right">
              Errors
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {bars.map((bar) => (
            <TableRow key={bar.start}>
              <TableRowHeader scope="row" className="h-10 text-xs">
                {bar.label}
              </TableRowHeader>
              <TableCell className="h-10 text-right tabular">
                {formatInteger(bar.ok + bar.errors)}
              </TableCell>
              <TableCell className="h-10 text-right tabular">{formatInteger(bar.errors)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
