import { Link } from "@tanstack/react-router";
import { Check, ChevronRight } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import type { ReactNode } from "react";

import { CostValue } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell";
import type { ExperimentRun } from "@/lib/api";
import { formatInteger, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatLatency, formatMean, type ScoreKind } from "./compare-view";
import {
  isActive,
  isFinished,
  runEndReason,
  runName,
  targetLabel,
  type VersionIndex,
} from "./run-label";
import { RunStatusPill } from "./run-status-pill";

/** The grid shared by the runs table's labels and rows (Figma "Experiments — Detail"). */
export const RUN_GRID =
  "lg:grid lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,0.6fr)_minmax(0,0.6fr)_minmax(0,0.8fr)_minmax(0,0.6fr)_minmax(0,0.7fr)_minmax(0,0.9fr)_16px] lg:items-center lg:gap-3";

export const NO_LATENCY = "Unknown: no item of this run answered yet.";

/** What every row of one experiment's runs table shares. */
export interface RunContext {
  experimentId: string;
  index: VersionIndex;
  /** How many items a full run reads, when the dataset is known. */
  expectedItems: number | null;
  score: { name: string; kind: ScoreKind } | null;
  /** The running run read live; list rows carry stored totals, null until a run ends. */
  liveRun: ExperimentRun | undefined;
}

interface RunRowProps {
  run: ExperimentRun;
  context: RunContext;
  ticked: boolean;
  onTick: (ticked: boolean) => void;
}

/** One run: tick it to compare, or open it. */
export function RunRow({ run, context, ticked, onTick }: RunRowProps) {
  const { orgId, projectId } = useProjectParams();
  const { experimentId } = context;
  const finished = isFinished(run);
  return (
    <li
      className={cn(
        "flex items-center gap-3 rounded-tile pl-4 transition-colors",
        ticked ? "bg-surface-selected" : "bg-surface-muted hover:bg-surface-hover",
      )}
    >
      <CheckboxPrimitive.Root
        checked={ticked}
        disabled={!finished}
        onCheckedChange={(next) => onTick(next === true)}
        aria-label={`Compare ${runName(run.number)}`}
        className="flex size-5 shrink-0 items-center justify-center rounded-checkbox border-[1.5px] border-subtle-foreground bg-surface disabled:opacity-40 data-[state=checked]:border-ink data-[state=checked]:bg-ink"
      >
        <CheckboxPrimitive.Indicator>
          <Check aria-hidden className="size-3.5 text-ink-foreground" strokeWidth={3} />
        </CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>
      <Link
        to="/$orgId/$projectId/experiments/$experimentId/runs/$runId"
        params={{ orgId, projectId, experimentId, runId: run.id }}
        className={cn(
          "grid min-w-0 flex-1 grid-cols-2 gap-x-3 gap-y-2 py-3.5 pr-4 text-sm sm:grid-cols-4",
          RUN_GRID,
        )}
      >
        <RunCells run={run} context={context} />
        <ChevronRight aria-hidden className="hidden size-4 text-muted-foreground lg:block" />
      </Link>
    </li>
  );
}

function RunCells({ run, context }: { run: ExperimentRun; context: RunContext }) {
  const { index, expectedItems, score, liveRun } = context;
  const totals = (liveRun?.id === run.id ? liveRun.totals : run.totals) ?? null;
  const stopped = run.status === "failed";
  const items = totals ? formatInteger(totals.items) : null;
  const partial = expectedItems !== null && totals !== null && totals.items < expectedItems;
  const stat = score && totals ? totals.scores[score.name] : undefined;
  return (
    <>
      <div className="col-span-2 flex min-w-0 flex-col gap-0.5 sm:col-span-4 lg:col-span-1">
        <span className="font-semibold text-foreground">{runName(run.number)}</span>
        <span
          className={cn("truncate text-xs", stopped ? "text-foreground" : "text-muted-foreground")}
        >
          {stopped ? runEndReason(run.error) : targetLabel(run.target, index)}
        </span>
      </div>
      <div>
        <RunStatusPill run={run} />
      </div>
      <Cell label="Items">
        {items === null ? (
          <UnknownValue
            reason={
              isActive(run) ? "Counting this run's items." : "Unknown: this run has no totals."
            }
          />
        ) : partial || isActive(run) ? (
          `${items} / ${formatInteger(expectedItems)}`
        ) : (
          items
        )}
      </Cell>
      <Cell label="Errors">
        {totals ? (
          <span className={cn(totals.errors > 0 && "text-danger-text")}>
            {formatInteger(totals.errors)}
          </span>
        ) : null}
      </Cell>
      <Cell label="Cost" mono>
        {totals ? <CostValue cost={totals.cost_usd} hasUnpriced={totals.has_unpriced} /> : null}
      </Cell>
      <Cell label="P95" mono>
        {totals
          ? (formatLatency(totals.latency_p95_ms) ?? <UnknownValue reason={NO_LATENCY} />)
          : null}
      </Cell>
      <Cell label={score?.name ?? "Score"} strong>
        {score === null ? null : stat?.mean !== undefined && stat.mean !== null ? (
          formatMean(stat.mean, score.kind)
        ) : (
          <UnknownValue reason="No item of this run has this score yet." />
        )}
      </Cell>
      <span className="text-muted-foreground">
        <span className="sr-only">Started </span>
        {formatRelativeTime(run.started_at)}
      </span>
    </>
  );
}

interface CellProps {
  label: string;
  mono?: boolean;
  strong?: boolean;
  children: ReactNode;
}

/** A value with its column name, which reads as a label on phones and is hidden on wide screens. */
function Cell({ label, mono, strong, children }: CellProps) {
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="text-overline text-subtle-foreground uppercase lg:sr-only">{label}</span>
      <span
        className={cn(
          "text-foreground tabular",
          mono && "font-mono text-xs",
          strong && "font-bold",
        )}
      >
        {children}
      </span>
    </span>
  );
}
