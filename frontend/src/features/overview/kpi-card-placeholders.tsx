import { EmptySparkline } from "@/components/sparkline";
import { Card } from "@/components/ui/card";

/** The ribbon's labels (kpis.ts `ribbonKpis`), so the first-run cards match the dashboard. */
const EMPTY_KPI_LABELS = ["Calls · error rate", "p95 latency", "Spend", "Doctor"] as const;

/**
 * First-run KPI cards: the ribbon's metrics with nothing to report yet, in `KpiCard`'s shape
 * (110 px, 12 px label, 28 px value, 36 px sparkline).
 */
export function EmptyKpiCards() {
  return (
    <ul aria-label="Key metrics" className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      {EMPTY_KPI_LABELS.map((label) => (
        <li key={label} className="min-w-0">
          <Card className="flex h-full min-h-[110px] min-w-0 flex-col gap-1 p-4">
            <p className="truncate text-xs font-medium text-muted-foreground">{label}</p>
            <p aria-hidden className="text-metric [font-size:1.75rem] text-subtle-foreground">
              —
            </p>
            <p className="text-xs text-subtle-foreground">No data yet</p>
            <div className="mt-auto pt-1">
              <EmptySparkline className="h-9" />
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}
