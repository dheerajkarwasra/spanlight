import { useId, type ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import type { Insight } from "@/lib/api";

import { BULLET_METRICS, bulletFor } from "./evidence-bullet";
import { EvidenceBulletChart } from "./evidence-bullet-chart";
import { metricTiles, type MetricTile } from "./evidence-metrics";
import { formatWindow } from "./insight-format";

interface EvidenceCardProps {
  insight: Pick<Insight, "kind" | "evidence">;
  /** Lines under the metric tiles, e.g. the provider-incident citation. */
  children?: ReactNode;
}

/**
 * Figma "Doctor/Evidence": the observed value against its baseline when the finding has one
 * (the chart's two numbers then leave the tiles), and the other numbers the detector saw.
 */
export function EvidenceCard({ insight, children }: EvidenceCardProps) {
  const titleId = useId();
  const { evidence } = insight;
  const bullet = bulletFor(insight);
  const charted = bullet === null ? [] : Object.values(BULLET_METRICS[insight.kind] ?? {});
  const tiles = metricTiles(evidence.metrics).filter((tile) => !charted.includes(tile.name));
  const window = formatWindow(evidence.window.start, evidence.window.end);
  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-5 p-5 sm:p-6">
      <div className="flex flex-col gap-1">
        <h2 id={titleId} className="text-card">
          Evidence
        </h2>
        {window === null ? null : (
          <p className="text-xs font-medium text-muted-foreground">{window}</p>
        )}
      </div>
      {bullet === null ? null : <EvidenceBulletChart bullet={bullet} />}
      {tiles.length === 0 ? null : (
        <ul aria-label="Metrics" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {tiles.map((tile) => (
            <MetricTileItem key={tile.name} tile={tile} />
          ))}
        </ul>
      )}
      {children}
    </Card>
  );
}

/** Figma "Doctor/Metric tile": overline label, big value, a hint. */
function MetricTileItem({ tile }: { tile: MetricTile }) {
  return (
    <li className="flex min-w-0 flex-col gap-1.5 rounded-tile bg-surface-muted px-4 py-3.5">
      <span className="text-overline text-subtle-foreground uppercase">{tile.label}</span>
      <span className="text-metric [overflow-wrap:anywhere] text-foreground tabular">
        {tile.value ?? <UnknownMetric label={tile.label} />}
      </span>
      {tile.hint === null ? null : (
        <span className="text-xs text-muted-foreground">{tile.hint}</span>
      )}
    </li>
  );
}

/** The "—" for a metric the detector could not compute, with the reason on focus and hover. */
function UnknownMetric({ label }: { label: string }) {
  const reason = `${label} was not available for this window`;
  return (
    <Tooltip content={reason}>
      <span tabIndex={0} aria-label={`Unknown: ${reason}`} className="cursor-help rounded-sm">
        —
      </span>
    </Tooltip>
  );
}
