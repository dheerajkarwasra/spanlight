import { Play } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { ColumnLabels, TileList, TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import type { ExperimentRun } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { RUN_GRID, RunRow, type RunContext } from "./run-row";

/** Rows shown before "Show all runs". */
const SHOWN = 20;

interface RunListProps {
  runs: { data: ExperimentRun[] | undefined; error: unknown; retry: () => void };
  context: RunContext;
  /** The ticked pair, in the URL as `?a=&b=`. */
  selection: { ticked: readonly string[]; onTick: (runId: string, ticked: boolean) => void };
}

/** The Runs card of Figma "Experiments — Detail": tick two runs to compare them. */
export function RunList({ runs, context, selection }: RunListProps) {
  if (runs.data === undefined) {
    return (
      <SectionCard title="Runs" description="Runs of this experiment">
        {runs.error ? (
          <ErrorState
            compact
            error={runs.error}
            title="Couldn't load runs"
            onRetry={runs.retry}
            className="rounded-tile bg-surface-muted"
          />
        ) : (
          <TileListSkeleton label="Loading runs" rows={3} className="h-[66px]" />
        )}
      </SectionCard>
    );
  }
  if (runs.data.length === 0) {
    return (
      <SectionCard title="Runs" description="Runs of this experiment">
        <EmptyState
          icon={Play}
          title="No runs yet"
          description="Start a run to send every dataset item through the target and score the answers. Each run is a real provider call."
          className="rounded-tile bg-surface-muted"
        />
      </SectionCard>
    );
  }
  return <Runs runs={runs.data} context={context} selection={selection} />;
}

function Runs({
  runs,
  context,
  selection,
}: Omit<RunListProps, "runs"> & { runs: ExperimentRun[] }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? runs : runs.slice(0, SHOWN);
  return (
    <SectionCard
      title="Runs"
      description={`${pluralize(runs.length, "run")} · tick two to compare them`}
      className="gap-3"
    >
      <ColumnLabels className={`pr-4 pl-12 ${RUN_GRID}`}>
        <span>Run</span>
        <span>Status</span>
        <span>Items</span>
        <span>Errors</span>
        <span>Cost</span>
        <span>P95</span>
        <span className="truncate">{context.score?.name ?? ""}</span>
        <span>Started</span>
        <span />
      </ColumnLabels>
      <TileList label="Runs">
        {shown.map((run) => (
          <RunRow
            key={run.id}
            run={run}
            context={context}
            ticked={selection.ticked.includes(run.id)}
            onTick={(next) => selection.onTick(run.id, next)}
          />
        ))}
      </TileList>
      {runs.length > shown.length ? (
        <Button size="sm" className="self-center" onClick={() => setShowAll(true)}>
          Show all {pluralize(runs.length, "run")}
        </Button>
      ) : null}
    </SectionCard>
  );
}
