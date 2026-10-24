import { KpiCard } from "@/components/kpi-card";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ValueOrUnknown } from "@/components/unknown-value";

import type { GatewayKpi, GatewayKpiLine } from "./overview-format";

const GRID_CLASSES = "grid gap-4 sm:grid-cols-2 lg:grid-cols-4";

/** The ribbon (spec ruling 20): requests, latency, cache hit rate, fallbacks and rate limits. */
export function OverviewKpis({ kpis }: { kpis: readonly GatewayKpi[] }) {
  return (
    <ul aria-label="Gateway metrics" className={GRID_CLASSES}>
      {kpis.map(({ id, label, value, unknownReason, delta, lines: [first, ...rest] }) => (
        <li key={id} className="min-w-0">
          <KpiCard
            label={label}
            value={value}
            unknownReason={unknownReason}
            delta={delta}
            denominator={first === undefined ? undefined : <Line line={first} />}
          >
            {rest.map((line, index) => (
              <p key={index} className="truncate text-xs text-muted-foreground tabular">
                <Line line={line} />
              </p>
            ))}
          </KpiCard>
        </li>
      ))}
    </ul>
  );
}

function Line({ line }: { line: GatewayKpiLine }) {
  if (typeof line === "string") {
    return line;
  }
  return (
    <>
      {line.prefix} <ValueOrUnknown value={line.value} reason={line.reason} />
    </>
  );
}

export function OverviewKpisSkeleton() {
  return (
    <ul aria-hidden className={GRID_CLASSES}>
      {Array.from({ length: 4 }, (_, index) => (
        <li key={index} className="min-w-0">
          <Card className="flex min-h-[110px] flex-col gap-2 p-4">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-7 w-20" />
            <Skeleton className="h-3 w-28" />
          </Card>
        </li>
      ))}
    </ul>
  );
}
