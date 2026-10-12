import { getRouteApi } from "@tanstack/react-router";

import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";

import { BudgetCard } from "./budget-card";
import { ConfigCard } from "./config-card";
import { EvaluatorHeader } from "./evaluator-header";
import { EVALUATORS_READ_ONLY, scoresAreBoolean } from "./evaluator-labels";
import {
  useAlertChannelsQuery,
  useCredentialsQuery,
  useEvaluatorQuery,
  useEvaluatorRunsQuery,
} from "./evaluator-queries";
import { RunsTable } from "./runs-table";

const detailRoute = getRouteApi("/_authed/$orgId/$projectId/evaluators/$evaluatorId");

/**
 * Figma "Evaluators — Detail" (budget 80 %, budget spent, member read-only, 375 px): the runs,
 * a judge's budget, and the configuration. Members see everything without the actions.
 */
export function EvaluatorDetailPage() {
  const { evaluatorId } = detailRoute.useParams();
  const canWrite = usePermission("evaluators:write");
  const query = useEvaluatorQuery(evaluatorId);
  const credentials = useCredentialsQuery().data;
  const channels = useAlertChannelsQuery().data;
  const newest = useEvaluatorRunsQuery(evaluatorId, undefined).data;

  if (query.isPending) {
    return (
      <div role="status" aria-label="Loading the evaluator" className="flex flex-col gap-6">
        <Skeleton className="h-20 rounded-card" />
        <Skeleton className="h-[480px] rounded-card" />
      </div>
    );
  }
  if (query.data === undefined) {
    return (
      <ErrorState
        error={query.error}
        title="Couldn't load this evaluator"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  const evaluator = query.data;
  const config = evaluator.config;
  const channelName =
    config.kind === "webhook"
      ? (channels?.find((channel) => channel.id === config.channel_id)?.name ?? null)
      : null;
  const lastRunAt =
    newest === undefined ? undefined : (newest.pages[0]?.items[0]?.started_at ?? null);

  return (
    <div className="flex flex-col gap-6">
      <EvaluatorHeader evaluator={evaluator} channelName={channelName} canWrite={canWrite} />
      {canWrite ? null : <ReadOnlyNote>{EVALUATORS_READ_ONLY}</ReadOnlyNote>}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <RunsTable evaluatorId={evaluator.id} booleanScores={scoresAreBoolean(evaluator)} />
        </div>
        <div className="flex flex-col gap-5 max-lg:order-first">
          {evaluator.kind === "llm_judge" ? <BudgetCard evaluatorId={evaluator.id} /> : null}
          <ConfigCard
            evaluator={evaluator}
            credentials={credentials}
            channels={channels}
            lastRunAt={lastRunAt}
          />
        </div>
      </div>
    </div>
  );
}
