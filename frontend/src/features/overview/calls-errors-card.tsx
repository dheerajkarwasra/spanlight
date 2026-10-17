import type { ReactNode } from "react";

import { ErrorState } from "@/components/error-state";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export const PLOT_HEIGHT_CLASSES = "h-[150px] sm:h-[196px]";

interface CallsErrorsCardProps {
  description?: ReactNode;
  totals?: ReactNode;
  className?: string;
  children: ReactNode;
}

/** Card chrome: title and description, the total pills (right on desktop, below on phones). */
export function CallsErrorsCard({
  description,
  totals,
  className,
  children,
}: CallsErrorsCardProps) {
  return (
    <Card className={cn("flex min-w-0 flex-col gap-3.5 p-5 sm:gap-4 sm:p-6", className)}>
      <div className="flex flex-col gap-3.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-col gap-0.5">
          <CardTitle>Calls &amp; errors</CardTitle>
          {description ? (
            <CardDescription className="mt-0 font-medium">{description}</CardDescription>
          ) : null}
        </div>
        {totals ? <div className="flex shrink-0 flex-wrap gap-2">{totals}</div> : null}
      </div>
      {children}
    </Card>
  );
}

export function CallsErrorsChartSkeleton({ className }: { className?: string }) {
  return (
    <CallsErrorsCard
      className={className}
      totals={
        <>
          <Skeleton className="h-[25px] w-20 rounded-full" />
          <Skeleton className="h-[25px] w-20 rounded-full" />
        </>
      }
    >
      <div aria-hidden className={cn(PLOT_HEIGHT_CLASSES, "flex flex-col justify-end gap-3")}>
        <Skeleton className="h-full w-full rounded-tile" />
      </div>
    </CallsErrorsCard>
  );
}

interface CallsErrorsChartErrorProps {
  error: unknown;
  onRetry: () => void;
  className?: string;
}

export function CallsErrorsChartError({ error, onRetry, className }: CallsErrorsChartErrorProps) {
  return (
    <CallsErrorsCard className={className}>
      <ErrorState compact error={error} onRetry={onRetry} title="Couldn't load this chart" />
    </CallsErrorsCard>
  );
}
