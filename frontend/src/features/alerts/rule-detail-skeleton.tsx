import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { AlertsBreadcrumbs } from "./rule-detail-header";

/** Figma "Rule detail — loading": the header, the ink hero and both cards as shimmering blocks. */
export function RuleDetailSkeleton() {
  return (
    <div role="status" aria-label="Loading alert rule" className="flex flex-col gap-6">
      <AlertsBreadcrumbs current="Rule" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-8 w-60 max-w-full" />
        <Skeleton className="h-4 w-48 max-w-full" />
      </div>
      <Card variant="hero" className="flex flex-col gap-5 p-5 sm:p-7">
        <Skeleton className="h-6 w-20 bg-rail-tile" />
        <Skeleton className="h-12 w-40 bg-rail-tile" />
        <div className="grid gap-3 sm:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-[67px] rounded-tile bg-rail-tile" />
          ))}
        </div>
      </Card>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Skeleton className="h-72 rounded-card" />
        <Skeleton className="h-56 rounded-card" />
      </div>
    </div>
  );
}
