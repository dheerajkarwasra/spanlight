/** The two runs of a comparison as the cards show them. Pure. */
import type { ExperimentRun, RunTotals } from "@/lib/api";

import { runName } from "./run-label";

/** One side of the comparison as the cards show it. */
export interface RunSide {
  /** "Run #3". */
  name: string;
  /** The run's totals; null while they load, when the read failed or before it ended. */
  totals: RunTotals | null;
  state: "loading" | "error" | "ready";
}

export interface SidePair {
  a: RunSide;
  b: RunSide;
}

/** Why a side's value is missing, for the "—" tooltip. */
export function sideReason(side: RunSide, ready: string): string {
  if (side.state === "loading") {
    return "Loading the run's totals.";
  }
  return side.state === "error" ? "Couldn't load this run's totals." : ready;
}

/** A side from its run read; `listed` names the run while the read is still on its way. */
export function sideOf(
  read: { data?: ExperimentRun | undefined; isError: boolean },
  listed: ExperimentRun | undefined,
): RunSide {
  return {
    name: runName(read.data?.number ?? listed?.number),
    totals: read.data?.totals ?? null,
    state: read.isError ? "error" : read.data ? "ready" : "loading",
  };
}
