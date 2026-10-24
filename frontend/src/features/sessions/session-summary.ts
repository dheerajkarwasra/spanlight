/** Aggregates for a session built from its traces. Pure functions only. */
import type { SessionTurn, TraceSummary } from "@/lib/api";
import { parseMoney } from "@/lib/format";

export interface SessionTotals {
  turns: number;
  /** Sum of known trace costs; null when no trace has a known cost. */
  costUsd: number | null;
  /** At least one trace has an unknown or partial cost, so the total is a lower bound. */
  costIsLowerBound: boolean;
  errors: number;
  firstAt: string | null;
  lastAt: string | null;
  /** From the first trace's start to the last trace's end. */
  durationMs: number | null;
}

/** Turns oldest first, for reading a conversation top to bottom. */
export function sortChronologically(turns: readonly SessionTurn[]): SessionTurn[] {
  return [...turns].sort(
    (a, b) => new Date(a.trace.started_at).getTime() - new Date(b.trace.started_at).getTime(),
  );
}

export function summariseSession(traces: readonly TraceSummary[]): SessionTotals {
  let costUsd: number | null = null;
  let costIsLowerBound = false;
  let errors = 0;
  let firstMs = Number.POSITIVE_INFINITY;
  let lastMs = Number.NEGATIVE_INFINITY;
  let firstAt: string | null = null;
  let lastAt: string | null = null;

  for (const trace of traces) {
    const cost = parseMoney(trace.cost_usd);
    if (cost === null) {
      costIsLowerBound = true;
    } else {
      costUsd = (costUsd ?? 0) + cost;
      if (trace.has_unpriced) {
        costIsLowerBound = true;
      }
    }
    errors += trace.error_count;

    const start = new Date(trace.started_at).getTime();
    const end = new Date(trace.ended_at).getTime();
    if (start < firstMs) {
      firstMs = start;
      firstAt = trace.started_at;
    }
    if (end > lastMs) {
      lastMs = end;
      lastAt = trace.ended_at;
    }
  }

  return {
    turns: traces.length,
    costUsd,
    // A lower bound only matters when there is a known total to qualify.
    costIsLowerBound: costUsd !== null && costIsLowerBound,
    errors,
    firstAt,
    lastAt,
    durationMs: firstAt !== null && lastAt !== null ? Math.max(lastMs - firstMs, 0) : null,
  };
}

/**
 * Turns recorded in an environment other than the selected one. The session detail shows every
 * turn whatever the environment filter, so the header says how many fall outside it.
 */
export function turnsOutsideEnvironment(
  traces: readonly TraceSummary[],
  environment: string | undefined,
): number {
  if (environment === undefined) {
    return 0;
  }
  return traces.filter((trace) => trace.environment !== environment).length;
}

/** Elapsed time between two ISO timestamps, or null if either is invalid. */
export function spanBetween(fromIso: string, toIso: string): number | null {
  const from = new Date(fromIso).getTime();
  const to = new Date(toIso).getTime();
  if (Number.isNaN(from) || Number.isNaN(to)) {
    return null;
  }
  return Math.max(to - from, 0);
}

export interface TokenTotals {
  input: number;
  output: number;
  total: number;
}

export function sumTokens(traces: readonly TraceSummary[]): TokenTotals {
  let input = 0;
  let output = 0;
  for (const trace of traces) {
    input += trace.input_tokens;
    output += trace.output_tokens;
  }
  return { input, output, total: input + output };
}

export interface ModelUsage {
  model: string;
  /** Turns (traces) that called this model at least once. */
  turns: number;
}

/** Models used in the session, most used first, then alphabetically. */
export function modelUsage(traces: readonly TraceSummary[]): ModelUsage[] {
  const counts = new Map<string, number>();
  for (const trace of traces) {
    for (const model of new Set(trace.models)) {
      counts.set(model, (counts.get(model) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([model, turns]) => ({ model, turns }))
    .sort((a, b) => b.turns - a.turns || a.model.localeCompare(b.model));
}

/**
 * The end user behind the session, when every trace that names one agrees.
 * Null when no trace has a user ID or the traces disagree.
 */
export function consistentUserId(traces: readonly TraceSummary[]): string | null {
  let userId: string | null = null;
  for (const trace of traces) {
    if (trace.external_user_id === null) {
      continue;
    }
    if (userId !== null && userId !== trace.external_user_id) {
      return null;
    }
    userId = trace.external_user_id;
  }
  return userId;
}

/** Cost per turn, only when every turn is priced; a partial total would understate it. */
export function averageCostPerTurn(totals: SessionTotals): number | null {
  if (totals.costUsd === null || totals.costIsLowerBound || totals.turns === 0) {
    return null;
  }
  return totals.costUsd / totals.turns;
}

export interface TimelinePoint {
  traceId: string;
  /** Position of the turn's start between the first and last turn, from 0 to 1. */
  offset: number;
  failed: boolean;
}

/** Where each turn started within the session, for the summary timeline strip. */
export function timelinePoints(traces: readonly TraceSummary[]): TimelinePoint[] {
  const starts = traces.map((trace) => new Date(trace.started_at).getTime());
  const valid = starts.filter((start) => !Number.isNaN(start));
  if (valid.length === 0) {
    return [];
  }
  const first = Math.min(...valid);
  const span = Math.max(...valid) - first;

  return traces.flatMap((trace, index) => {
    const start = starts[index];
    if (start === undefined || Number.isNaN(start)) {
      return [];
    }
    return [
      {
        traceId: trace.trace_id,
        offset: span === 0 ? 0.5 : (start - first) / span,
        failed: trace.error_count > 0,
      },
    ];
  });
}

/** Running total of the known turn costs, oldest first; null until a turn has a known cost. */
export function cumulativeCost(traces: readonly TraceSummary[]): (number | null)[] {
  let total: number | null = null;
  return traces.map((trace) => {
    const cost = parseMoney(trace.cost_usd);
    if (cost !== null) total = (total ?? 0) + cost;
    return total;
  });
}
