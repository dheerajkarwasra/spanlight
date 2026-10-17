import { formatUsd } from "@/features/budgets/budget-period";
import { REPLAY_OFF_REASON } from "@/features/traces/replay-button-state";
import type { ReplayBudget, ReplayResult } from "@/lib/api";
import { parseMoney, pluralize } from "@/lib/format";

import { MAX_TOKENS_RULE, parseParam } from "./config-state";

/*
 * Pure rules of the playground's replay column: what blocks "Run replay", how the result's
 * fidelity and the month's budget read. The server enforces every one of them again.
 */

/** What ingest writes in place of secrets and over-deep values; a request holding one is refused. */
export const MARKERS = ["[REDACTED]", "[MAX_DEPTH]"] as const;

/** The most output tokens a replay may ask for (spec §6.3). */
export const MAX_REPLAY_TOKENS = 4096;

export const MARKERS_REASON = "Remove or replace the redacted parts before replaying.";
export const INVALID_JSON_REASON = "Fix the JSON before replaying.";
export const BUDGET_SPENT_REASON = "Skipped: this month's replay budget is spent.";
export const NO_CREDENTIAL_REASON = "Pick a credential to replay with.";
export const NO_MODEL_REASON = "Enter the model to replay with.";
export const MAX_TOKENS_REASON = "Use 1 to 4096 max tokens.";

export interface MarkerRange {
  start: number;
  end: number;
  marker: (typeof MARKERS)[number];
}

/** Every marker in `text`, in order of appearance. */
export function markerRanges(text: string): MarkerRange[] {
  const ranges: MarkerRange[] = [];
  for (const marker of MARKERS) {
    for (let at = text.indexOf(marker); at !== -1; at = text.indexOf(marker, at + marker.length)) {
      ranges.push({ start: at, end: at + marker.length, marker });
    }
  }
  return ranges.sort((a, b) => a.start - b.start);
}

/** "2 markers left: [REDACTED] on line 5, [MAX_DEPTH] on line 8."; null when none is left. */
export function markersLeftText(text: string): string | null {
  const ranges = markerRanges(text);
  if (ranges.length === 0) {
    return null;
  }
  const places = ranges.map(
    (range) => `${range.marker} on line ${String(text.slice(0, range.start).split("\n").length)}`,
  );
  return `${pluralize(ranges.length, "marker")} left: ${places.join(", ")}.`;
}

/** The request text as a JSON object, or null when it is not valid JSON or not an object. */
export function parseRequestBody(text: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/**
 * Whether the request still holds a marker, in the text as typed or once parsed: a JSON escape
 * such as `[REDACTED]` hides one from the text but not from the parsed value.
 */
export function hasMarkers(text: string): boolean {
  if (markerRanges(text).length > 0) {
    return true;
  }
  const body = parseRequestBody(text);
  return body !== null && markerRanges(JSON.stringify(body)).length > 0;
}

/** What "Run replay" depends on. */
export interface ReplayFormState {
  /** The request that will be sent: the stored body's JSON, or the user's edit of it. */
  text: string;
  credentialId: string | null;
  /** The model to send; required (an OTLP span may have none recorded). */
  model: string;
  /** The max tokens field; empty sends the stored value. */
  maxTokens: string;
  running: boolean;
  /** The month's replay budget; undefined while loading or when it could not be read. */
  budget: ReplayBudget | undefined;
  /** The server has replay turned off (`409 NOT_CONFIGURED` or a zero budget). */
  replayOff: boolean;
}

/** Spent or held to the last cent: no replay can pass the budget until the month turns. */
export function isBudgetSpent(budget: ReplayBudget | undefined): boolean {
  const remaining = parseMoney(budget?.remaining_usd);
  return remaining !== null && remaining <= 0;
}

/** Why "Run replay" is disabled, or null when it can run (or is running). */
export function runDisabledReason(state: ReplayFormState): string | null {
  if (state.replayOff) return REPLAY_OFF_REASON;
  if (isBudgetSpent(state.budget)) return BUDGET_SPENT_REASON;
  if (hasMarkers(state.text)) return MARKERS_REASON;
  if (parseRequestBody(state.text) === null) return INVALID_JSON_REASON;
  if (state.credentialId === null) return NO_CREDENTIAL_REASON;
  if (state.model.trim() === "") return NO_MODEL_REASON;
  if (parseParam(state.maxTokens, MAX_TOKENS_RULE) === null) return MAX_TOKENS_REASON;
  return null;
}

export function canRun(state: ReplayFormState): boolean {
  return !state.running && runDisabledReason(state) === null;
}

/** The stored output cap of a request body, whichever key the provider uses; null without one. */
export function storedMaxTokens(body: Record<string, unknown> | null): number | null {
  for (const key of ["max_tokens", "max_completion_tokens", "max_output_tokens"]) {
    const value = body?.[key];
    if (typeof value === "number" && Number.isInteger(value)) {
      return value;
    }
  }
  return null;
}

function budgetUsed(budget: ReplayBudget): string | null {
  const spent = parseMoney(budget.spent_usd);
  const reserved = parseMoney(budget.reserved_usd);
  // Rounded to the ledger's 8 decimals so float sums never print as $0.30000000000000004.
  return spent === null || reserved === null ? null : (spent + reserved).toFixed(8);
}

/** "$1.20 of $5.00 this month": settled spend plus what running replays hold. */
export function budgetText(budget: ReplayBudget): string {
  const used = formatUsd(budgetUsed(budget)) ?? "—";
  return `${used} of ${formatUsd(budget.monthly_budget_usd) ?? "—"} this month`;
}

/** The used share of the month's budget, 0 to 1, for the meter's bar; null when unknown. */
export function budgetRatio(budget: ReplayBudget): number | null {
  const used = parseMoney(budgetUsed(budget));
  const total = parseMoney(budget.monthly_budget_usd);
  return used === null || total === null || total <= 0 ? null : Math.min(used / total, 1);
}

const resetFormat = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
  timeZone: "UTC",
});

/** "$4.97 spent and $0.03 reserved of $5.00 this month. The budget resets on November 1 (UTC)." */
export function budgetSpentDetail(budget: ReplayBudget | undefined): string {
  const none = "No provider request was made.";
  if (budget === undefined) {
    return none;
  }
  const start = new Date(budget.month_start);
  const reset = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1));
  const figures = `${formatUsd(budget.spent_usd) ?? "—"} spent and ${formatUsd(budget.reserved_usd) ?? "—"} reserved of ${formatUsd(budget.monthly_budget_usd) ?? "—"} this month.`;
  const resets = Number.isNaN(reset.getTime())
    ? ""
    : ` The budget resets on ${resetFormat.format(reset)} (UTC).`;
  return `${figures}${resets} ${none}`;
}

type Body = Record<string, unknown> | null;

function capChange(key: string, before: Body, after: Body): string {
  const stored = before?.[key];
  const asked = after?.[key];
  if (typeof stored !== "number") {
    return `${key} set`;
  }
  const ceiling =
    typeof asked === "number" ? Math.min(asked, MAX_REPLAY_TOKENS) : MAX_REPLAY_TOKENS;
  return `${key} ${String(stored)} → ${String(Math.min(stored, ceiling))}`;
}

const CHANGE_WORDS: Record<string, string> = {
  stream: "stream removed",
  stream_options: "stream options removed",
  n: "n set to 1",
  model: "model changed",
  body: "request edited",
};

/**
 * "stream removed, max_tokens 8192 → 4096, model changed": the server's `changed` list in words.
 * `before` is the request the replay started from (stored or edited) and `after` carries the
 * overrides asked for (`max_tokens`, `model`); without them a cap reads "max_tokens set".
 */
export function changedText(changed: readonly string[], before: Body, after: Body): string {
  return changed
    .map((key) =>
      key.startsWith("max_")
        ? capChange(key, before, after)
        : (CHANGE_WORDS[key] ?? `${key} changed`),
    )
    .join(", ");
}

export const IDENTICAL_LINE = "Exact replay — identical request";
export const NOT_EXACT_LINE = "Not exact: this span was recorded without its request parameters";

/**
 * The result's fidelity in one line. "Identical" is claimed only from `same_request_hash`
 * (spec §6.5), never from the fidelity label alone: it can be null (not comparable).
 */
export function fidelityLine(
  result: ReplayResult,
  before: Body = null,
  after: Body = null,
): string {
  if (result.fidelity === "messages_only") {
    return NOT_EXACT_LINE;
  }
  if (result.fidelity === "modified") {
    const changes = changedText(result.changed, before, after);
    return changes === "" ? "Modified" : `Modified: ${changes}`;
  }
  if (result.same_request_hash === true) {
    return IDENTICAL_LINE;
  }
  return result.same_request_hash === false
    ? "Exact replay — but the request sent hashes differently from the stored one"
    : "Exact replay — not checked against the stored request";
}
