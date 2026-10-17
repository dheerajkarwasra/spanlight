import { api } from "./client";
import { projectPath } from "./paths";
import type { Page } from "./types";

/** One day's selection. Counts are keyed by reason (`doctor_finding`, `error`, …). */
export interface ReviewSelection {
  /** The UTC day selected, `YYYY-MM-DD`. */
  day: string;
  /** Null once the queue was deleted; the history outlives it. */
  queue_id: string | null;
  /** Traces that matched each reason before the cap. */
  candidates: Record<string, number>;
  /** Items queued per reason. */
  queued: Record<string, number>;
  /** Traces already in the queue, left out. */
  skipped_duplicates: number;
  /** True when the daily cap cut the selection short. */
  cap_hit: boolean;
  ran_at: string;
  duration_ms: number | null;
}

export interface ReviewPolicy {
  id: string;
  queue_id: string;
  queue_name: string;
  enabled: boolean;
  /** The random share, a 4-place decimal string in [0, 0.05]. */
  random_rate: string;
  include_errors: boolean;
  include_cost_outliers: boolean;
  include_doctor_findings: boolean;
  daily_cap: number;
  /** The environments read; empty means all but the internal ones. */
  environments: string[];
  /** 01:00 UTC of the next day; null while the policy is disabled. */
  next_run_at: string | null;
  last_selection: ReviewSelection | null;
  created_at: string;
  updated_at: string;
}

/** The body of `PUT /review-policy`; an omitted field takes its default, not its old value. */
export interface ReviewPolicyInput {
  queue_id: string;
  enabled?: boolean;
  /** A decimal string with at most 4 places in [0, 0.05], e.g. `"0.0100"`. Default 1%. */
  random_rate?: string;
  include_errors?: boolean;
  include_cost_outliers?: boolean;
  include_doctor_findings?: boolean;
  /** 1 to 500; default 50. */
  daily_cap?: number;
  /** At most 20, none of `evals`, `playground`, `experiments`, `doctor`. */
  environments?: string[];
}

/** A queued manual selection (202). */
export interface ReviewRunRequested {
  day: string;
  job_id: number;
}

export interface ReviewSelectionListQuery {
  /** At most 30. */
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

function policyPath(projectId: string): string {
  return `${projectPath(projectId)}/review-policy`;
}

/**
 * The project's daily review policy under `/projects/{id}/review-policy`. Reads need
 * `project:read`; setting, deleting and running it need `annotations:write` (member). Errors:
 * `404 NOT_FOUND` (no policy, or a queue outside the project), `422 QUEUE_LACKS_FAILURE_CATEGORY`,
 * `422 NOTHING_SELECTED`, `422 VALIDATION_ERROR`, `422 DAY_NOT_COMPLETE`, `422 DAY_TOO_OLD`,
 * `409 ALREADY_SELECTED`.
 */
export const reviewApi = {
  /** `404 NOT_FOUND` while the project has no policy. */
  get: (projectId: string): Promise<ReviewPolicy> => api.get<ReviewPolicy>(policyPath(projectId)),
  /** Creates or replaces the policy. */
  put: (projectId: string, input: ReviewPolicyInput): Promise<ReviewPolicy> =>
    api.put<ReviewPolicy>(policyPath(projectId), input),
  /** The selection history stays. */
  delete: (projectId: string): Promise<void> => api.delete(policyPath(projectId)),
  /** Queues the selection of a completed UTC day (`YYYY-MM-DD`), yesterday to 7 days back. */
  requestRun: (projectId: string, day: string): Promise<ReviewRunRequested> =>
    api.post<ReviewRunRequested>(`${policyPath(projectId)}/runs`, { day }),
  /** Newest day first. */
  selections: (
    projectId: string,
    query: ReviewSelectionListQuery = {},
  ): Promise<Page<ReviewSelection>> =>
    api.get<Page<ReviewSelection>>(`${policyPath(projectId)}/selections`, { ...query }),
};
