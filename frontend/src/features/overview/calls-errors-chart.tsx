import { ChartDataTableView } from "@/components/chart-data-table";
import { Badge } from "@/components/ui/badge";
import type { Bucket } from "@/lib/api";
import { formatCompact, formatInteger } from "@/lib/format";
import type { Band } from "@/lib/incident-bands";
import { cn } from "@/lib/utils";

import { CallsErrorsCard, PLOT_HEIGHT_CLASSES } from "./calls-errors-card";
import { CallsErrorsLegend, type IncidentFeed } from "./calls-errors-legend";
import { CallsErrorsPlot } from "./calls-errors-plot";
import { CallsErrorsTable } from "./calls-errors-table";
import { callsTable } from "./calls-table";
import {
  bucketAdjective,
  bucketNoun,
  hasCallData,
  summarizeCalls,
  type ChartPoint,
} from "./chart-data";
import { incidentSummary } from "./incident-overlay";
import { markerLines, type ReleaseMarker } from "./release-markers";
import { useCallsView, useIncidentOverlay } from "./use-incident-overlay";

export interface CallsChartData {
  points: ChartPoint[];
  bucket: Bucket;
  /** e.g. "last 24h", for the phone subtitle. */
  rangeLabel: string;
  now: Date;
}

interface CallsErrorsChartProps {
  data: CallsChartData;
  /** API-affected incidents of the providers in use, clipped; undefined while unknown. */
  bands?: Band[] | undefined;
  /** This server does not poll the status feeds: a note replaces the switch. */
  pollingOff?: boolean;
  /** Releases first seen in the window: dashed markers. */
  releases?: readonly ReleaseMarker[];
  /** Why there are no release markers (a window over 30 days), shown under the legend. */
  releasesNote?: string | null;
  isRefreshing: boolean;
  className?: string;
}

function incidentFeed(bands: Band[] | undefined, pollingOff: boolean): IncidentFeed {
  if (pollingOff) {
    return "polling-off";
  }
  return bands === undefined ? "unknown" : "ready";
}

/**
 * Figma "Calls & errors card" with the "Incident overlay": calls per bucket with the failed ones
 * stacked on top, provider
 * incidents shaded behind them (switchable, remembered per viewer), and a "Show table" view that
 * lists the same numbers and incidents per bucket.
 */
export function CallsErrorsChart({
  data,
  bands,
  pollingOff = false,
  releases = [],
  releasesNote = null,
  isRefreshing,
  className,
}: CallsErrorsChartProps) {
  const { points, bucket, rangeLabel, now } = data;
  const [overlayOn, setOverlayOn] = useIncidentOverlay();
  const [view, setView] = useCallsView();
  const feed = incidentFeed(bands, pollingOff);
  const shownBands = feed === "ready" && overlayOn ? (bands ?? []) : [];
  const { calls, errors } = summarizeCalls(points);
  const callsLabel = `${formatCompact(calls) ?? "0"} ${calls === 1 ? "call" : "calls"}`;
  const errorsLabel = `${formatInteger(errors) ?? "0"} ${errors === 1 ? "error" : "errors"}`;
  const table = callsTable(points, bucket, feed === "ready" && overlayOn ? shownBands : null);
  const incidents = incidentSummary(shownBands);
  const releaseLines = markerLines(releases, points, bucket);
  const label = `LLM calls and failed calls per ${bucketNoun(bucket)}: ${callsLabel}, ${errorsLabel} in total${
    incidents ? `; ${incidents}` : ""
  }`;

  return (
    <CallsErrorsCard
      className={className}
      description={
        <>
          <span className="sm:hidden">
            {bucketAdjective(bucket)} · {rangeLabel}
          </span>
          <span className="max-sm:hidden">
            {bucketAdjective(bucket)} · {rangeLabel} · failed calls stack on top
          </span>
        </>
      }
      totals={
        <>
          <Badge variant="accent" className="tabular">
            {callsLabel}
          </Badge>
          <Badge variant="danger" className="tabular">
            {errorsLabel}
          </Badge>
        </>
      }
    >
      <CallsErrorsLegend
        incidents={{
          feed,
          shown: overlayOn,
          onShownChange: setOverlayOn,
          hasBands: shownBands.length > 0,
          hasCritical: shownBands.some((band) => band.critical),
        }}
        view={view}
        onViewChange={setView}
        partialBucket={points.some((point) => point.partial) ? bucket : null}
        releases={{ shown: releaseLines.length > 0, note: releasesNote }}
      />
      <div className={cn("transition-opacity", isRefreshing && "opacity-60")}>
        {!hasCallData(points) ? (
          <div
            className={cn(
              PLOT_HEIGHT_CLASSES,
              "flex items-center justify-center rounded-tile border border-dashed border-border text-sm text-muted-foreground",
            )}
          >
            No LLM calls in this period
          </div>
        ) : view === "table" ? (
          <CallsErrorsTable table={table} bucket={bucket} />
        ) : (
          <>
            <CallsErrorsPlot
              points={points}
              bucket={bucket}
              now={now}
              bands={shownBands}
              releaseLines={releaseLines}
              label={label}
            />
            <ChartDataTableView table={table} />
          </>
        )}
      </div>
    </CallsErrorsCard>
  );
}
