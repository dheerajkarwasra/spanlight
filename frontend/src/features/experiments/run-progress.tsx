import { CostValue } from "@/components/cost-value";
import { Callout } from "@/components/callout";
import type { ExperimentRun } from "@/lib/api";
import { formatCost, formatInteger } from "@/lib/format";

import { formatElapsed, runDurationMs, runEndReason, runName } from "./run-label";
import { RunStatusPill } from "./run-status-pill";

interface RunProgressProps {
  /** The run read with its totals (they are computed from the items while it runs). */
  run: ExperimentRun;
  expectedItems: number | null;
  maxCost: string;
}

/** Figma "Experiments — Detail — run in progress": how far the newest run got, refreshed live. */
export function RunProgress({ run, expectedItems, maxCost }: RunProgressProps) {
  const done = run.totals?.items ?? 0;
  const ratio = expectedItems ? Math.min(1, done / expectedItems) : null;
  const name = runName(run.number);
  // Screen readers hear quarters only, not every 3-second refresh.
  const quarter = ratio === null ? null : Math.floor(ratio * 4) * 25;
  return (
    <section
      aria-label={`${name} progress`}
      className="flex flex-col gap-3 rounded-card border border-border bg-surface-selected p-5 shadow-card"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <RunStatusPill run={run} />
        <h2 className="text-card">
          {run.status === "queued" ? `${name} is queued` : `${name} is running`}
        </h2>
        <p className="ml-auto text-sm font-bold text-accent tabular">
          {expectedItems
            ? `${formatInteger(done)} of ${formatInteger(expectedItems)} items done`
            : `${formatInteger(done)} items done`}
        </p>
      </div>
      <p className="sr-only" aria-live="polite">
        {quarter === null || quarter === 0
          ? `${name} started.`
          : `${name}: ${quarter}% of items done.`}
      </p>
      <div
        role="progressbar"
        aria-label={`${name} items done`}
        aria-valuemin={0}
        aria-valuemax={expectedItems ?? undefined}
        aria-valuenow={done}
        className="h-2 overflow-hidden rounded-full border border-border-strong bg-surface"
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-500 motion-reduce:transition-none"
          style={{ width: ratio === null ? "0%" : `${ratio * 100}%` }}
        />
      </div>
      <p className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
        <span>
          Cost so far{" "}
          <CostValue
            cost={run.totals?.cost_usd ?? null}
            hasUnpriced={run.totals?.has_unpriced}
            unknownReason="Unknown: no item of this run has a known cost yet."
          />{" "}
          of the {formatCost(maxCost)} limit
        </span>
        <span>Elapsed {formatElapsed(runDurationMs(run))}</span>
        <span>Updates every few seconds. Four items run at a time.</span>
      </p>
    </section>
  );
}

/** Figma "Experiments — Detail — cost limit reached", and the same note for other early ends. */
export function RunStopped({ run, expectedItems, maxCost }: RunProgressProps) {
  const done = formatInteger(run.totals?.items ?? 0);
  const of = expectedItems ? ` of ${formatInteger(expectedItems)}` : "";
  if (run.error === "cost_cap_reached") {
    return (
      <Callout tone="warning" title={`${runName(run.number)} stopped: cost limit reached`}>
        {done}
        {of} items ran before the next item could pass the {formatCost(maxCost)} limit. The finished
        items are kept and can still be compared. The limit is part of the experiment, so create a
        new experiment with a higher limit to finish the rest.
      </Callout>
    );
  }
  return (
    <Callout tone="danger" title={`${runName(run.number)} ended early`}>
      {runEndReason(run.error)}. {done}
      {of} items finished; they are kept and can still be compared.
    </Callout>
  );
}
