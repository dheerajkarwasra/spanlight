import type { ReactNode } from "react";

import { CostValue, UNPRICED_OR_NO_USAGE_REASON } from "@/components/cost-value";
import { UnknownValue } from "@/components/unknown-value";
import type { ExperimentRun } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatLatency } from "./compare-view";
import { formatElapsed, isActive, runDurationMs } from "./run-label";

const NO_LATENCY = "Unknown: no item of this run answered, so there is no latency.";

interface TileProps {
  label: string;
  value: ReactNode;
  note?: string;
  danger?: boolean;
}

function Tile({ label, value, note, danger }: TileProps) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-tile border border-border bg-surface p-4 shadow-card">
      <dt className="text-overline text-subtle-foreground uppercase">{label}</dt>
      <dd
        className={cn(
          "text-[28px] leading-tight font-bold tracking-[-0.03em] tabular",
          danger ? "text-danger-text" : "text-foreground",
        )}
      >
        {value}
      </dd>
      {note ? <dd className="text-xs text-muted-foreground">{note}</dd> : null}
    </div>
  );
}

/** Figma "Experiments — Run" metric tiles: items, errors, cost, latency and run time. */
export function RunTotals({ run }: { run: ExperimentRun }) {
  const totals = run.totals;
  if (totals === null) {
    return null;
  }
  const answered = totals.items - totals.errors;
  const elapsed = formatElapsed(runDurationMs(run));
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      <Tile
        label="Items"
        value={formatInteger(totals.items)}
        note={`${formatInteger(answered)} with output`}
      />
      <Tile
        label="Errors"
        value={formatInteger(totals.errors)}
        danger={totals.errors > 0}
        note={totals.errors > 0 ? "Shown in the table below" : undefined}
      />
      <Tile
        label="Cost"
        value={
          <CostValue
            cost={totals.cost_usd}
            hasUnpriced={totals.has_unpriced}
            unknownReason={UNPRICED_OR_NO_USAGE_REASON}
          />
        }
        note="Price book, per call"
      />
      <Tile
        label="Latency p50"
        value={formatLatency(totals.latency_p50_ms) ?? <UnknownValue reason={NO_LATENCY} />}
      />
      <Tile
        label="Latency p95"
        value={formatLatency(totals.latency_p95_ms) ?? <UnknownValue reason={NO_LATENCY} />}
      />
      <Tile
        label="Run time"
        value={elapsed ?? <UnknownValue reason="Unknown: the run has no recorded end." />}
        note={isActive(run) ? "So far · four items at a time" : "Four items at a time"}
      />
    </dl>
  );
}
