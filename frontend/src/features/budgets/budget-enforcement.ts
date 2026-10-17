/*
 * How a `block` budget really behaves: a soft limit, as measured in the 2026-10-14 pilot report
 * (finding D9). Every surface that describes blocking reads these numbers, so a re-measurement
 * edits the constants and nothing else.
 */

/** How often budgets are evaluated (the backend's `guard.EVALUATION_INTERVAL_S`). */
export const EVALUATION_INTERVAL_S = 60;
export const MEASURED_LAG_S = 85.5;
export const MEASURED_OVERSHOOT = "6×";
export const MEASURED_SPEND = "$0.0122";
export const MEASURED_LIMIT = "$0.002";
export const MEASURED_ON = "2026-10-14";

/** What a blocked gateway call gets; the dialog sets it in mono. */
export const BLOCKED_RESPONSE = "402 BUDGET_EXCEEDED";

/** The row badge's tooltip: the first two sentences of the dialog hint. */
export function softLimitBadgeTooltip(): string {
  return `Soft limit. Gateway calls in this scope get ${BLOCKED_RESPONSE} after spend is measured above the amount.`;
}

/** Spec §8.4: the dialog hint for action = Block. */
export function softLimitHint(): string {
  return (
    `${softLimitBadgeTooltip()} Spend is measured every ${EVALUATION_INTERVAL_S} seconds, so ` +
    `calls keep going through until the next pass: in the ${MEASURED_ON} pilot a ` +
    `${MEASURED_LIMIT} budget started blocking after ${MEASURED_LAG_S} s and spend reached ` +
    `${MEASURED_SPEND} (${MEASURED_OVERSHOOT}). For a limit that must hold, set the amount ` +
    `below it. Traces sent with the SDK are never blocked.`
  );
}

/** The budgets page note under the list. */
export function softLimitNote(): string {
  return `Blocking is a soft limit: spend is measured every ${EVALUATION_INTERVAL_S} s. Nothing sent with the SDK is ever blocked.`;
}
