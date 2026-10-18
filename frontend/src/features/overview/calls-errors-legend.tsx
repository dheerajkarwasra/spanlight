import { useId } from "react";

import { SeriesKey } from "@/components/chart-parts";
import { ChartViewToggle } from "@/components/chart-view-toggle";
import { Switch } from "@/components/ui/switch";
import type { Bucket } from "@/lib/api";
import { PARTIAL_OPACITY, SERIES_COLORS } from "@/lib/chart-theme";
import type { ChartView } from "@/lib/use-stored-choice";

import { IncidentLegendItems } from "./incident-bands";

/**
 * Where the incident overlay stands: `unknown` while the incidents are loading or failed (no
 * switch), `polling-off` when this server does not poll the status feeds, `ready` otherwise.
 */
export type IncidentFeed = "unknown" | "polling-off" | "ready";

export interface IncidentControls {
  feed: IncidentFeed;
  shown: boolean;
  onShownChange: (shown: boolean) => void;
  /** Whether any band is drawn (legend items appear only then), and whether one is critical. */
  hasBands: boolean;
  hasCritical: boolean;
}

interface CallsErrorsLegendProps {
  incidents: IncidentControls;
  view: ChartView;
  onViewChange: (view: ChartView) => void;
  /** The "Partial hour/day" key renders only when a partial bucket is drawn. */
  partialBucket: Bucket | null;
}

/** The legend row: series keys, incident keys, then "Show table" and "Show incidents". */
export function CallsErrorsLegend({
  incidents,
  view,
  onViewChange,
  partialBucket,
}: CallsErrorsLegendProps) {
  const switchId = useId();
  const { feed, shown, hasBands } = incidents;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <ul
        aria-label="Legend"
        className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs font-medium"
      >
        <li className="flex items-center gap-1.5">
          <SeriesKey color={SERIES_COLORS.calls} shape="bar" />
          <span>Succeeded</span>
        </li>
        <li className="flex items-center gap-1.5">
          <SeriesKey color={SERIES_COLORS.errors} shape="bar" />
          <span>Failed</span>
        </li>
        {partialBucket ? (
          <li className="flex items-center gap-1.5">
            <span className="flex" style={{ opacity: PARTIAL_OPACITY }}>
              <SeriesKey color={SERIES_COLORS.calls} shape="bar" />
            </span>
            <span>Partial {partialBucket}</span>
          </li>
        ) : null}
        {feed === "ready" && shown && hasBands ? (
          <IncidentLegendItems hasCritical={incidents.hasCritical} />
        ) : null}
      </ul>
      <div className="ml-auto flex flex-wrap items-center gap-x-4">
        <ChartViewToggle view={view} onChange={onViewChange} />
        {feed === "ready" ? (
          <label
            htmlFor={switchId}
            className="flex min-h-11 cursor-pointer items-center gap-2 text-sm font-medium"
          >
            Show incidents
            <Switch id={switchId} checked={shown} onCheckedChange={incidents.onShownChange} />
          </label>
        ) : null}
      </div>
      {feed === "polling-off" ? (
        <p className="w-full text-xs font-medium text-muted-foreground">
          Provider status feeds are off on this server (
          <code className="font-mono">PROVIDER_STATUS_POLLING_ENABLED=false</code>)
        </p>
      ) : null}
    </div>
  );
}
