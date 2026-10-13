import { Info } from "lucide-react";

import { ValueOrUnknown } from "@/components/unknown-value";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { fittedMetricFontSize } from "@/lib/metric-fit";

import type { GatewayKpi } from "./overview-format";

const TILE_CLASSES = "flex min-h-32 flex-col gap-1.5 p-5 sm:p-6";

/**
 * Six equal tiles: requests, error rate, cache hit rate, fallbacks and the two p95 times. The
 * values share one size, fitted to the widest of them, so none is ever cut short (T7-07).
 */
export function OverviewKpis({ kpis }: { kpis: readonly GatewayKpi[] }) {
  const valueFontSize = fittedMetricFontSize(kpis.map((kpi) => kpi.value));
  return (
    <ul aria-label="Gateway metrics" className="grid grid-cols-2 gap-4 lg:grid-cols-3">
      {kpis.map((kpi) => (
        <li key={kpi.id} className="min-w-0">
          <KpiTile kpi={kpi} valueFontSize={valueFontSize} />
        </li>
      ))}
    </ul>
  );
}

function KpiTile({ kpi, valueFontSize }: { kpi: GatewayKpi; valueFontSize: string | undefined }) {
  return (
    <Card className={TILE_CLASSES}>
      <p className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
        {kpi.label}
        {kpi.info ? <InfoButton label={kpi.label} text={kpi.info} /> : null}
      </p>
      {/* The size container the fitted font size is measured against. */}
      <div className="@container">
        <p
          data-kpi-value
          style={valueFontSize ? { fontSize: valueFontSize } : undefined}
          className="text-metric whitespace-nowrap text-foreground"
        >
          <ValueOrUnknown value={kpi.value} reason={kpi.unknownReason} />
        </p>
      </div>
      <p className="text-xs font-medium text-subtle-foreground tabular">{kpi.detail}</p>
      {kpi.extra ? <p className="text-xs font-medium text-accent tabular">{kpi.extra}</p> : null}
    </Card>
  );
}

/** The info icon behind a tile label. A 24 px hit area keeps it reachable on touch screens. */
function InfoButton({ label, text }: { label: string; text: string }) {
  return (
    <Tooltip content={text}>
      <button
        type="button"
        aria-label={`About ${label}: ${text}`}
        className="-my-1 inline-flex size-6 items-center justify-center rounded-full text-subtle-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Info aria-hidden className="size-3.5" />
      </button>
    </Tooltip>
  );
}

export function OverviewKpisSkeleton() {
  return (
    <ul aria-hidden className="grid grid-cols-2 gap-4 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <li key={index} className="min-w-0">
          <Card className={TILE_CLASSES}>
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-9 w-28" />
            <Skeleton className="h-3 w-32" />
          </Card>
        </li>
      ))}
    </ul>
  );
}
