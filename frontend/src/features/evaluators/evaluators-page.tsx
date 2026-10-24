import { Link } from "@tanstack/react-router";
import { Info, ListChecks, Plus } from "lucide-react";
import { useId } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { PageHeader } from "@/components/page-header";
import { ReadOnlyLine } from "@/components/read-only-line";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { LiveDemoButton } from "@/features/auth";
import { ScoreSettingsLinks } from "@/features/scores";
import { usePermission, useProjectParams } from "@/features/shell";
import { pluralize } from "@/lib/format";

import { EVALUATORS_READ_ONLY } from "./evaluator-labels";
import { EvaluatorList } from "./evaluator-list";
import { useAlertChannelsQuery, useEvaluatorsQuery } from "./evaluator-queries";

/** "New evaluator": a link to the form, or a disabled button for members. */
function NewEvaluatorButton({ canWrite, readOnlyId }: { canWrite: boolean; readOnlyId: string }) {
  const { orgId, projectId } = useProjectParams();
  if (!canWrite) {
    return (
      <Button variant="primary" disabled aria-describedby={readOnlyId}>
        <Plus aria-hidden />
        New evaluator
      </Button>
    );
  }
  return (
    <Button variant="primary" asChild>
      <Link to="/$orgId/$projectId/evaluators/new" params={{ orgId, projectId }}>
        <Plus aria-hidden />
        New evaluator
      </Link>
    </Button>
  );
}

/**
 * Figma "Evaluators — List" and its states (empty, loading, error, 375 px). Every member sees the
 * evaluators and the judges' spend; admins and owners create and change them.
 */
export function EvaluatorsPage() {
  const canWrite = usePermission("evaluators:write");
  const readOnlyId = useId();
  const query = useEvaluatorsQuery();
  const evaluators = query.data ?? [];
  const channels = useAlertChannelsQuery().data;
  const button = <NewEvaluatorButton canWrite={canWrite} readOnlyId={readOnlyId} />;
  const enabled = evaluators.filter((evaluator) => evaluator.enabled).length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Evaluators"
        description={
          <>
            Score traces automatically with a rule, an LLM judge or your own webhook. A judge never
            spends more than its monthly budget. <ScoreSettingsLinks />
          </>
        }
        actions={
          <div className="flex flex-col items-stretch gap-2 sm:items-end">
            {button}
            {canWrite ? null : <ReadOnlyLine id={readOnlyId}>{EVALUATORS_READ_ONLY}</ReadOnlyLine>}
          </div>
        }
      />
      <SectionCard
        title="Evaluators"
        description={
          query.data && query.data.length > 0
            ? `${pluralize(query.data.length, "evaluator")}${query.hasNextPage ? " shown" : ""} · ${enabled} enabled · sampled every minute`
            : undefined
        }
        className="gap-3"
      >
        {query.isPending ? (
          <TileListSkeleton label="Loading evaluators" rows={5} className="h-[66px]" />
        ) : query.data === undefined ? (
          <ErrorState
            compact
            error={query.error}
            title="Couldn't load evaluators"
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : query.data.length === 0 ? (
          <EmptyState
            icon={ListChecks}
            title="No evaluators yet"
            description="An evaluator scores traces for you: a rule you write, an LLM judge under a monthly budget, or your own webhook."
            action={canWrite ? button : undefined}
            setup={<LiveDemoButton />}
            className="rounded-tile bg-surface-muted"
          />
        ) : (
          <>
            <EvaluatorList evaluators={query.data} channels={channels} />
            {query.hasNextPage ? (
              <ListFooter
                summary={`Showing ${pluralize(query.data.length, "evaluator")}`}
                hasNextPage
                isFetchingNextPage={query.isFetchingNextPage}
                onLoadMore={() => {
                  void query.fetchNextPage();
                }}
                layout="card"
              />
            ) : null}
            <p className="flex items-center gap-2 px-1 pt-1 text-xs font-medium text-muted-foreground">
              <Info aria-hidden className="size-3.5 shrink-0" />
              Judge evaluators skip runs once their monthly budget is spent. Scores show on the
              trace as Eval · name.
            </p>
          </>
        )}
      </SectionCard>
    </div>
  );
}
