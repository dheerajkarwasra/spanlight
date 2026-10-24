import { GitCompareArrows, Tag } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { isApiError, type ReleaseStats } from "@/lib/api";

import { ErrorClassDiff } from "./error-class-diff";
import { KpiDeltaTable } from "./kpi-delta-table";
import { ModelMix } from "./model-mix";
import { NewErrors } from "./new-errors";
import { SAME_RELEASE_NOTICE } from "./release-picker";
import type { useReleaseCompareQuery, useReleaseListQuery } from "./releases-queries";

const ONE_RELEASE = "Only one release in this window.";

/**
 * The release a 404 UNKNOWN_RELEASE is about: the one of the pair missing from the window's
 * release list (the API's message doesn't say which). `undefined` for any other error.
 */
function unknownRelease(
  error: unknown,
  pair: readonly [string, string],
  known: readonly ReleaseStats[],
): string | undefined {
  if (!isApiError(error) || error.code !== "UNKNOWN_RELEASE") {
    return undefined;
  }
  return pair.find((release) => !known.some((stats) => stats.release === release)) ?? pair[1];
}

interface CompareBodyProps {
  list: ReturnType<typeof useReleaseListQuery>;
  compare: ReturnType<typeof useReleaseCompareQuery>;
  a: string | undefined;
  b: string | undefined;
}

/** The comparison of the pair, or why there is none: loading, error, no or one release, no pick. */
export function CompareBody({ list, compare, a, b }: CompareBodyProps) {
  if (list.isPending) {
    return <ResultSkeleton />;
  }
  if (list.data === undefined) {
    return (
      <ErrorState
        error={list.error}
        title="Couldn't load releases"
        onRetry={() => void list.refetch()}
      />
    );
  }
  if (list.data.length === 0) {
    return <NoReleases />;
  }
  if (a === undefined && b === undefined && list.data.length === 1) {
    return (
      <EmptyState
        icon={Tag}
        title={ONE_RELEASE}
        description="Widen the time range to compare it with an earlier release."
      />
    );
  }
  if (a === undefined || b === undefined) {
    return (
      <EmptyState
        icon={GitCompareArrows}
        title="Pick two releases"
        description="Choose a baseline and a candidate above, then select Compare."
      />
    );
  }
  if (a === b) {
    return (
      <EmptyState
        icon={GitCompareArrows}
        title="Pick two releases"
        description={SAME_RELEASE_NOTICE}
      />
    );
  }
  if (compare.isPending) {
    return <ResultSkeleton />;
  }
  if (compare.data === undefined) {
    const missing = unknownRelease(compare.error, [a, b], list.data);
    if (missing !== undefined) {
      return (
        <EmptyState
          icon={Tag}
          title={`Release ${missing} has no traces in this window.`}
          description="Pick another release or change the time range."
        />
      );
    }
    return (
      <ErrorState
        error={compare.error}
        title="Couldn't compare these releases"
        onRetry={() => void compare.refetch()}
      />
    );
  }
  const comparison = compare.data;
  return (
    <div className="flex flex-col gap-6">
      <KpiDeltaTable comparison={comparison} />
      <div className="grid gap-6 lg:grid-cols-2">
        <ModelMix mix={comparison.model_mix} />
        <ErrorClassDiff classes={comparison.error_classes} />
      </div>
      <NewErrors errors={comparison.new_errors} a={comparison.a.release} b={comparison.b.release} />
    </div>
  );
}

const CODE = "rounded-sm bg-surface-muted px-1 py-0.5 font-mono text-xs text-foreground";

function NoReleases() {
  return (
    <EmptyState
      icon={Tag}
      title="No releases in this window"
      description={
        <>
          Set <code className={CODE}>release</code> on your traces (SDK{" "}
          <code className={CODE}>release=</code>, OTLP <code className={CODE}>service.version</code>
          , gateway header <code className={CODE}>x-spanlight-release</code>).
        </>
      }
    />
  );
}

function ResultSkeleton() {
  return (
    <div role="status" aria-label="Loading comparison" className="flex flex-col gap-6">
      <Skeleton className="h-96 rounded-card" />
      <div className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-72 rounded-card" />
        <Skeleton className="h-72 rounded-card" />
      </div>
    </div>
  );
}
