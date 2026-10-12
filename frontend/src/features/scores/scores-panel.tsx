import { Plus, Star } from "lucide-react";
import { useId } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";
import { formatInteger } from "@/lib/format";

import { AddScoreDialog } from "./add-score-dialog";
import { ScoreGroups } from "./score-groups";
import { useScoreConfigsQuery, useTraceScoresQuery } from "./scores-queries";

const EMPTY_TEXT = "Add one by hand, or set up an evaluator to score traces automatically.";

/**
 * Figma "Trace detail" Scores card, under the usage card: every score of the trace grouped by
 * name, latest first, with the source of each and an "Add score" action for members.
 */
export function ScoresPanel({ traceId }: { traceId: string }) {
  const titleId = useId();
  const scoresQuery = useTraceScoresQuery(traceId);
  const configsQuery = useScoreConfigsQuery();
  const canWrite = usePermission("scores:write");
  const count = scoresQuery.data?.length ?? 0;

  function body() {
    if (scoresQuery.isPending) {
      return <ScoresSkeleton />;
    }
    if (scoresQuery.isError) {
      return (
        <ErrorState
          compact
          error={scoresQuery.error}
          title="Couldn't load the scores"
          onRetry={() => {
            void scoresQuery.refetch();
          }}
        />
      );
    }
    if (count === 0) {
      return (
        <EmptyState
          icon={Star}
          title="No scores yet"
          description={EMPTY_TEXT}
          className="rounded-tile bg-surface-muted py-10"
          action={
            canWrite ? (
              <AddScoreDialog
                traceId={traceId}
                trigger={<Button variant="primary">Add score</Button>}
              />
            ) : null
          }
        />
      );
    }
    return <ScoreGroups scores={scoresQuery.data} configs={configsQuery.data} />;
  }

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex min-h-10 items-center gap-2">
        <CardTitle id={titleId}>Scores</CardTitle>
        {count > 0 ? (
          <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-full border border-border-strong px-1.5 text-2xs font-semibold text-muted-foreground tabular">
            <span className="sr-only">, </span>
            {formatInteger(count)}
          </span>
        ) : null}
        {canWrite && count > 0 ? (
          <AddScoreDialog
            traceId={traceId}
            trigger={
              <Button variant="secondary" className="ml-auto">
                Add score
              </Button>
            }
          />
        ) : null}
      </div>
      {body()}
    </Card>
  );
}

/** The span panel's Scores tab: the scores sent for this span, and a way to add one. */
export function SpanScores({ traceId, spanId }: { traceId: string; spanId: string }) {
  const scoresQuery = useTraceScoresQuery(traceId);
  const configsQuery = useScoreConfigsQuery();
  const canWrite = usePermission("scores:write");
  const scores = scoresQuery.data?.filter((score) => score.span_id === spanId) ?? [];

  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-center gap-1.5 text-overline text-muted-foreground uppercase">
        Scores
        <span className="text-xs font-medium tracking-normal normal-case">
          on this span · {formatInteger(scores.length)}
        </span>
      </p>
      {scoresQuery.isPending ? (
        <ScoresSkeleton />
      ) : scoresQuery.isError ? (
        <ErrorState
          compact
          error={scoresQuery.error}
          title="Couldn't load the scores"
          onRetry={() => {
            void scoresQuery.refetch();
          }}
        />
      ) : scores.length === 0 ? (
        <p className="rounded-tile bg-surface-muted px-4 py-3.5 text-sm text-muted-foreground">
          No scores on this span yet.
        </p>
      ) : (
        <ScoreGroups scores={scores} configs={configsQuery.data} />
      )}
      {canWrite ? (
        <AddScoreDialog
          traceId={traceId}
          spanId={spanId}
          trigger={
            <Button variant="secondary" className="w-full">
              <Plus aria-hidden />
              Add score to this span
            </Button>
          }
        />
      ) : null}
    </div>
  );
}

function ScoresSkeleton() {
  return (
    <div role="status" aria-label="Loading scores" className="flex flex-col gap-2">
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-[86px] rounded-tile" />
      <Skeleton className="h-[86px] rounded-tile" />
    </div>
  );
}
