import { Link } from "@tanstack/react-router";
import { CircleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import { errorMessage, isApiError } from "@/lib/api";

import { DoctorBreadcrumbs } from "./insight-header";

/** Figma "Doctor — Insight detail — loading": blocks shaped like the sections. */
export function InsightDetailSkeleton() {
  return (
    <div role="status" aria-label="Loading insight" className="flex flex-col gap-6">
      <DoctorBreadcrumbs current="Finding" />
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-56 rounded-full" />
        <Skeleton className="h-9 w-full max-w-2xl" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Skeleton className="h-40 rounded-card" />
        <Skeleton className="h-40 rounded-card" />
      </div>
      <Skeleton className="h-72 rounded-card" />
      <Skeleton className="h-32 rounded-card" />
    </div>
  );
}

/**
 * Figma "Doctor — Insight detail — error": the insight is gone (404, no retry) or the request
 * failed (the API's message and "Try again").
 */
export function InsightLoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { orgId, projectId } = useProjectParams();
  const gone = isApiError(error) && error.isNotFound;
  return (
    <div className="flex flex-col gap-6">
      <DoctorBreadcrumbs current="Finding" />
      <div
        role="alert"
        className="flex flex-col items-center gap-4 rounded-card bg-surface-muted px-6 py-12 text-center"
      >
        <span className="flex size-12 items-center justify-center rounded-full bg-danger-subtle">
          <CircleAlert aria-hidden className="size-5 text-danger" strokeWidth={2} />
        </span>
        <div className="flex max-w-md flex-col gap-1.5">
          <h1 className="text-xl font-bold tracking-[-0.01em] text-foreground">
            {gone ? "This insight no longer exists" : "Couldn't load this insight"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {gone ? "It may have been removed after its retention period." : errorMessage(error)}
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-2.5">
          {gone ? null : (
            <Button variant="primary" size="sm" onClick={onRetry}>
              Try again
            </Button>
          )}
          <Button size="sm" variant={gone ? "primary" : undefined} asChild>
            <Link to="/$orgId/$projectId/doctor" params={{ orgId, projectId }}>
              Back to the Doctor
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
