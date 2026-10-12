import { Activity } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ListFooter } from "@/components/list-footer";
import { SectionCard } from "@/components/section-card";
import { ColumnLabels, TileList, TileListSkeleton } from "@/components/tile-list";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { pluralize } from "@/lib/format";

import { useEvaluatorRunsQuery } from "./evaluator-queries";
import {
  RUN_FILTERS,
  RUN_STATE_LABELS,
  runFilterStatus,
  runState,
  type RunFilter,
} from "./run-copy";
import { RUN_COLUMNS } from "./run-columns";
import { RunRow } from "./run-row";

const FILTER_OPTIONS = RUN_FILTERS.map((value) => ({
  value,
  label: value === "all" ? "All" : RUN_STATE_LABELS[value],
}));

const EMPTY_COPY: Record<RunFilter, string> = {
  all: "No runs yet. Sampled traces are scored within a minute or two; a test run shows up here too.",
  done: "No finished runs yet.",
  failed: "No failed runs.",
  running: "Nothing is being scored right now.",
  queued: "Nothing is waiting to be scored.",
  skipped: "No runs were skipped for budget.",
};

/**
 * Figma "Runs": newest first, filtered by state, with "Load more". A run skipped for budget is
 * its own neutral state (spec §11), so "Failed" leaves those out and "Skipped" shows only them.
 */
interface RunsTableProps {
  evaluatorId: string;
  /** The evaluator's scores read Yes/No (its pass/fail rules). */
  booleanScores: boolean;
}

export function RunsTable({ evaluatorId, booleanScores }: RunsTableProps) {
  const [filter, setFilter] = useState<RunFilter>("all");
  const query = useEvaluatorRunsQuery(evaluatorId, runFilterStatus(filter));
  const runs = (query.data?.pages ?? [])
    .flatMap((page) => page.items)
    .filter((run) => filter === "all" || runState(run) === filter);

  return (
    <SectionCard
      title="Runs"
      description="Newest first, refreshed every minute. Runs skipped for budget are shown as Skipped."
      className="@container gap-4"
    >
      <SegmentedControl
        aria-label="Show runs"
        tone="ink"
        value={filter}
        options={FILTER_OPTIONS}
        onValueChange={setFilter}
        className="w-full overflow-x-auto sm:w-fit"
      />
      {query.isPending ? (
        <TileListSkeleton label="Loading runs" rows={6} className="h-[60px]" />
      ) : query.data === undefined ? (
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load runs"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : runs.length === 0 && !query.hasNextPage ? (
        <EmptyState
          icon={Activity}
          title="No runs"
          description={EMPTY_COPY[filter]}
          className="rounded-tile bg-surface-muted"
        />
      ) : (
        <div className="flex flex-col gap-1.5">
          <ColumnLabels className="px-4 @[40rem]:flex @[40rem]:gap-4">
            <span className={RUN_COLUMNS.trace}>Trace</span>
            <span className={RUN_COLUMNS.status}>Status</span>
            <span className={RUN_COLUMNS.result}>Result</span>
            <span className={RUN_COLUMNS.cost}>Cost</span>
          </ColumnLabels>
          <TileList label="Runs">
            {runs.map((run) => (
              <RunRow key={run.id} run={run} booleanScores={booleanScores} />
            ))}
          </TileList>
          <ListFooter
            summary={`Showing ${pluralize(runs.length, "run")}`}
            hasNextPage={query.hasNextPage}
            isFetchingNextPage={query.isFetchingNextPage}
            onLoadMore={() => {
              void query.fetchNextPage();
            }}
            layout="card"
          />
        </div>
      )}
      <p className="px-1 text-xs font-medium text-muted-foreground">
        A running judge call that stops reporting for 10 minutes is marked failed as abandoned.
      </p>
    </SectionCard>
  );
}
