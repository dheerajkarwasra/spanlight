/*
 * Why a retry count or retry cost is unknown ("—"), shared by Traces and Provider health so both
 * explain a dash in the same words. `retry_calls` is null for a trace stored before retries were
 * counted; `retry_cost_usd` alone is null when a retried call has no known price.
 */

export const RETRY_NOT_COMPUTED_REASON = "not computed for traces stored before 2026-10-15";
export const RETRY_NO_KNOWN_COST_REASON = "a retried call has no known cost";
