import { ChartViewToggle } from "@/components/chart-view-toggle";
import { ErrorState } from "@/components/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import type { BudgetBurn as BudgetBurnData } from "@/lib/api";
import { useChartView } from "@/lib/use-stored-choice";

import { formatUsd, percentLabel } from "./budget-period";
import { shortUsd } from "./budget-ticks";
import { useBudgetBurnQuery } from "./budgets-queries";
import { BurnChart } from "./burn-chart";
import { burnForecast, burnPoints, burnTooltip, dayLabel } from "./burn-series";

/** Per viewer, per browser: every burn panel opens the way the last one was left. */
const BURN_VIEW_KEY = "spanlight-budget-burn-view";

const NO_PRICED_SPEND_REASON = "No priced spend in this period up to this day.";

const NO_FORECAST_REASON =
  "Needs 3 complete days of priced spend in this period; a day with unpriced spend leaves it unknown.";

interface BudgetBurnProps {
  budgetId: string;
  /** The panel's id, for the "Show burn" button's `aria-controls`. */
  id: string;
}

/**
 * Spec ruling 11, the "Show burn" disclosure: cumulative spend per day since the period start,
 * fetched when the panel opens, as a line or a table.
 */
export function BudgetBurn({ budgetId, id }: BudgetBurnProps) {
  const query = useBudgetBurnQuery(budgetId);
  const [view, setView] = useChartView(BURN_VIEW_KEY);
  const burn = query.data;
  const since = burn ? dayLabel(burn.period_start.slice(0, 10)) : null;

  return (
    <div id={id} className="flex flex-col gap-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <p className="text-[13px] font-semibold text-foreground">
            Cumulative spend{since ? ` since ${since}` : ""}
          </p>
          {burn ? (
            <p className="text-xs font-medium text-muted-foreground">
              Line: spend to date
              {burnForecast(burn)?.usd != null ? ", dashes after today: forecast" : ""} · dashed:
              amount {shortUsd(Number(burn.amount_usd))} · dotted: 50 and 80 %
            </p>
          ) : null}
          {burn ? <ForecastLine burn={burn} /> : null}
        </div>
        {burn ? <ChartViewToggle view={view} onChange={setView} /> : null}
      </div>
      {query.isPending ? (
        <Skeleton className="h-[200px] rounded-tile" />
      ) : query.isError ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load the burn line"
          onRetry={() => void query.refetch()}
        />
      ) : view === "table" ? (
        <BurnTable burn={query.data} />
      ) : (
        <BurnChart
          burn={query.data}
          points={burnPoints(query.data)}
          summary={summary(query.data)}
        />
      )}
    </div>
  );
}

/**
 * "Forecast $465 by Oct 31 · average of the last 7 days", or the day spend passes the amount in
 * the warning colour. Nothing for a daily budget; an unknown forecast is "—", never $0.
 */
function ForecastLine({ burn }: { burn: BudgetBurnData }) {
  const forecast = burnForecast(burn);
  if (!forecast) return null;
  const by = dayLabel(forecast.lastDay);
  const amount = shortUsd(Number(burn.amount_usd));
  const usd = forecast.usd === null ? null : shortUsd(forecast.usd);
  const { crossing } = forecast;
  if (crossing) {
    const verb = crossing.passed ? "Passed" : "Passes";
    return (
      <p className="text-xs font-medium text-warning">
        <span className="font-semibold">
          {verb} {amount} on {dayLabel(crossing.day)}
        </span>{" "}
        · forecast {usd === null ? <UnknownValue reason={NO_FORECAST_REASON} /> : usd} by {by}
      </p>
    );
  }
  return (
    <p className="text-xs font-medium text-muted-foreground">
      {usd === null ? (
        <>
          Forecast <UnknownValue reason={NO_FORECAST_REASON} /> by {by}
        </>
      ) : (
        <>
          <span className="font-semibold text-foreground">
            Forecast {usd} by {by}
          </span>{" "}
          · average of the last {forecast.basisDays} days
        </>
      )}
    </p>
  );
}

/** "Spend to date per day since Oct 1. Latest: Oct 14 · $182 of $500 (36 %)". */
function summary(burn: BudgetBurnData): string {
  const known = burnPoints(burn).filter((point) => point.cumulative !== null);
  const latest = known.at(-1);
  const start = `Spend to date per day since ${dayLabel(burn.period_start.slice(0, 10))}.`;
  const forecast = burnForecast(burn);
  const ahead =
    forecast?.usd != null
      ? `. Forecast ${shortUsd(forecast.usd)} by ${dayLabel(forecast.lastDay)}.`
      : "";
  return latest
    ? `${start} Latest: ${burnTooltip(latest)}${ahead}`
    : `${start} No priced spend yet.`;
}

function BurnTable({ burn }: { burn: BudgetBurnData }) {
  const amount = Number(burn.amount_usd);
  return (
    <div className="max-h-[260px] overflow-y-auto">
      <table className="w-full text-[13px] tabular">
        <caption className="sr-only">Spend to date per day, UTC</caption>
        <thead className="text-left text-xs font-medium text-muted-foreground">
          <tr>
            <th scope="col" className="py-1.5 font-medium">
              Day (UTC)
            </th>
            <th scope="col" className="py-1.5 text-right font-medium">
              Spend to date
            </th>
            <th scope="col" className="py-1.5 text-right font-medium">
              Of {shortUsd(amount)}
            </th>
          </tr>
        </thead>
        <tbody className="text-foreground">
          {burn.points.map((point) => {
            const cumulative = formatUsd(point.cumulative_usd);
            return (
              <tr key={point.day} className="border-t border-border">
                <td className="py-1.5">{dayLabel(point.day)}</td>
                <td className="py-1.5 text-right">
                  {cumulative ?? <UnknownValue reason={NO_PRICED_SPEND_REASON} />}
                </td>
                <td className="py-1.5 text-right text-muted-foreground">
                  {cumulative === null ? null : percentLabel(Number(point.cumulative_usd) / amount)}
                </td>
              </tr>
            );
          })}
          <ForecastRow burn={burn} />
        </tbody>
      </table>
    </div>
  );
}

/** The table's last row: "Forecast, Oct 31 · $465 · 93 %"; none without a known forecast. */
function ForecastRow({ burn }: { burn: BudgetBurnData }) {
  const forecast = burnForecast(burn);
  if (forecast?.usd == null) return null;
  return (
    <tr className="border-t border-border-strong text-muted-foreground">
      <td className="py-1.5">Forecast, {dayLabel(forecast.lastDay)}</td>
      <td className="py-1.5 text-right">{shortUsd(forecast.usd)}</td>
      <td className="py-1.5 text-right">{percentLabel(forecast.usd / Number(burn.amount_usd))}</td>
    </tr>
  );
}
