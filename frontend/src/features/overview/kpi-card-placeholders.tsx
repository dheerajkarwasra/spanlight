import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { EmptySparkline } from "./sparkline";

/** The shared shape of a KPI card, so placeholders and real cards line up. */
export const CARD_LAYOUT =
  "flex h-full min-h-[174px] min-w-0 flex-col gap-1.5 p-4 sm:py-5 sm:pr-5 sm:pl-6";

/** Loading placeholders shaped like the four KPI cards. */
export function KpiCardsSkeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cn("grid grid-cols-2 gap-3 sm:gap-4", className)}>
      {Array.from({ length: 4 }, (_, index) => (
        <Card key={index} className={CARD_LAYOUT}>
          <div className="flex items-center justify-between">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-6 w-14 rounded-full" />
          </div>
          <Skeleton className="h-9 w-24" />
          <Skeleton className="h-3 w-28" />
          <Skeleton className="mt-auto h-8 w-full" />
        </Card>
      ))}
    </div>
  );
}

const EMPTY_KPI_LABELS = ["LLM calls", "p95 latency", "Error rate", "Spend"] as const;

/** First-run KPI cards: the metrics the page will show, with nothing to report yet. */
export function EmptyKpiCards() {
  return (
    <ul aria-label="Key metrics" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {EMPTY_KPI_LABELS.map((label) => (
        <li key={label} className="min-w-0">
          <Card className={CARD_LAYOUT}>
            <p className="text-sm font-medium text-muted-foreground">{label}</p>
            <p aria-hidden className="text-metric text-subtle-foreground">
              —
            </p>
            <p className="text-xs font-medium text-subtle-foreground">No data yet</p>
            <div className="mt-auto pt-1.5">
              <EmptySparkline />
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}
