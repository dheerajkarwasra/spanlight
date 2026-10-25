import { Link } from "@tanstack/react-router";
import { ThumbsUp } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { PrecisionKind } from "@/lib/api";

import { usePrecision } from "./doctor-queries";
import {
  hasAnswers,
  intervalFootnote,
  PRECISION_TARGET,
  precisionErrorText,
  sortPrecisionRows,
  targetLine,
} from "./precision-format";
import { PrecisionCards } from "./precision-cards";
import { PrecisionTable } from "./precision-table";

interface PrecisionTabProps {
  /** "Go to open insights" from the empty state. */
  onOpenInsights: () => void;
}

/**
 * Figma "Doctor/Precision": how often each check was right, from members' "Was this right?"
 * answers over all time, against the target line.
 */
export function PrecisionTab({ onOpenInsights }: PrecisionTabProps) {
  const query = usePrecision();
  const rows = useMemo(
    () => (query.data === undefined ? undefined : sortPrecisionRows(query.data.kinds)),
    [query.data],
  );

  if (query.isPending) {
    return <PrecisionSkeleton />;
  }
  if (rows === undefined) {
    return (
      <Card>
        <ErrorState
          compact
          error={query.error}
          title="Precision didn’t load"
          description={precisionErrorText(query.error)}
          retrying={query.isFetching}
          onRetry={() => {
            void query.refetch();
          }}
        />
      </Card>
    );
  }
  if (!hasAnswers(rows)) {
    return (
      <Card>
        <EmptyState
          icon={ThumbsUp}
          title="No answers yet"
          description={`Open an insight and answer “Was this right?”. Each check gets a precision once its findings have answers; a check needs ${String(PRECISION_TARGET.minAnswers)} before it is marked.`}
          action={
            <Button variant="primary" onClick={onOpenInsights}>
              Go to open insights
            </Button>
          }
        />
      </Card>
    );
  }
  return <PrecisionCard rows={rows} />;
}

function PrecisionCard({ rows }: { rows: readonly PrecisionKind[] }) {
  const ranges = intervalFootnote(rows);
  return (
    <SectionCard
      title="How often each check is right"
      description={
        <>
          <span className="hidden sm:inline">
            From your team’s answers to “Was this right?”, all time. Precision = right ÷ answers;
            the bar is the Wilson 95&nbsp;% interval.{" "}
          </span>
          {targetLine()}
        </>
      }
      className="gap-4"
    >
      <Legend />
      <div className="hidden sm:block">
        <PrecisionTable rows={rows} />
      </div>
      <div className="sm:hidden">
        <PrecisionCards rows={rows} />
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        {ranges === null ? null : `${ranges} `}
        Rows under {PRECISION_TARGET.minAnswers} answers are greyed and never marked. “—”: no
        answers, so no precision.
      </p>
      {/* Spec ruling 9: the dial for a check that is wrong too often. */}
      <Link
        from="/$orgId/$projectId/doctor"
        to="."
        search={(prev) => ({ ...prev, tab: "sensitivity" as const })}
        className="inline-flex min-h-6 items-center self-start rounded-sm text-sm font-semibold text-accent hover:underline max-md:min-h-11"
      >
        Too many wrong findings? Change sensitivity
      </Link>
    </SectionCard>
  );
}

/** What the strip's marks mean (desktop). */
function Legend() {
  return (
    <ul
      aria-label="Legend"
      className="hidden flex-wrap items-center gap-x-5 gap-y-1 text-xs font-medium text-muted-foreground sm:flex"
    >
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="size-2 rounded-full bg-accent" />
        Precision
      </li>
      <li className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="h-2 w-5 rounded-full border-2 border-accent bg-accent-subtle"
        />
        Wilson 95&nbsp;% interval
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-3 w-0.5 bg-foreground" />
        Target {PRECISION_TARGET.precision}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-3 border-l border-dashed border-subtle-foreground" />
        Lower-bound floor {PRECISION_TARGET.lowerBound} (dashed)
      </li>
    </ul>
  );
}

function PrecisionSkeleton() {
  return (
    <Card
      className="flex flex-col gap-3 p-5 sm:p-6"
      aria-busy="true"
      aria-label="Loading precision"
    >
      <Skeleton className="h-4 w-64" />
      <Skeleton className="h-3 w-full" />
      {[0, 1, 2, 3].map((row) => (
        <Skeleton key={row} className="h-11 rounded-tile" />
      ))}
    </Card>
  );
}
