import { api } from "./client";
import { projectPath } from "./paths";

export type ScoreDataType = "numeric" | "categorical" | "boolean";
/** `api` is the SDK or REST, `human` a person on the dashboard, `eval` an evaluator run. */
export type ScoreSource = "api" | "human" | "eval";

/** A number, or the same number as a decimal string (up to four decimals, within ±1e9). */
export type ScoreNumber = number | string;

export interface ScoreConfig {
  id: string;
  name: string;
  data_type: ScoreDataType;
  /** Numeric only; null is an open end. */
  min_value: number | null;
  max_value: number | null;
  /** Categorical only; empty is an open list. */
  categories: string[];
  description: string | null;
  /** True while the config is the one the first score with this name created. */
  implicit: boolean;
  created_at: string;
  updated_at: string;
}

export interface ScoreConfigCreate {
  /** Lower case letters, digits and `_ . -`; at most 64 characters. */
  name: string;
  data_type: ScoreDataType;
  min_value?: ScoreNumber | null;
  max_value?: ScoreNumber | null;
  categories?: string[];
  description?: string | null;
}

export interface ScoreConfigUpdate {
  data_type?: ScoreDataType | null;
  min_value?: ScoreNumber | null;
  max_value?: ScoreNumber | null;
  categories?: string[] | null;
  description?: string | null;
}

export interface Score {
  id: string;
  trace_id: string;
  span_id: string | null;
  name: string;
  data_type: ScoreDataType;
  /** Numeric and boolean (1/0) scores. */
  value: number | null;
  /** Categorical scores. */
  string_value: string | null;
  source: ScoreSource;
  comment: string | null;
  /** The person behind a `human` score; null for other sources or a deleted user. */
  author_user_id: string | null;
  author_name: string | null;
  evaluator_run_id: string | null;
  created_at: string;
}

/** A trace's latest score of one name, as the trace list carries it. */
export interface ScoreSummary {
  name: string;
  value: number | null;
  string_value: string | null;
}

/** Send exactly one of `value` (a number, or true/false) and `string_value`. */
export interface ScoreCreate {
  /** Makes a retry idempotent. */
  id?: string | null;
  span_id?: string | null;
  name: string;
  value?: boolean | ScoreNumber | null;
  string_value?: string | null;
  comment?: string | null;
}

function configsPath(projectId: string): string {
  return `${projectPath(projectId)}/score-configs`;
}

function traceScoresPath(projectId: string, traceId: string): string {
  return `${projectPath(projectId)}/traces/${encodeURIComponent(traceId)}/scores`;
}

/**
 * Scores and their configs. Reads need `project:read`; writing a score or a score config
 * needs `scores:write` (member). Errors: `409 NAME_TAKEN`, `409 SCORE_CONFIG_IN_USE`,
 * `422 VALIDATION_ERROR`.
 */
export const scoresApi = {
  configs: (projectId: string): Promise<ScoreConfig[]> =>
    api.get<ScoreConfig[]>(configsPath(projectId)),
  createConfig: (projectId: string, input: ScoreConfigCreate): Promise<ScoreConfig> =>
    api.post<ScoreConfig>(configsPath(projectId), input),
  /**
   * Replaces the fields sent; the name never changes. A new `data_type` is `409
   * SCORE_CONFIG_IN_USE` once scores carry the name.
   */
  updateConfig: (
    projectId: string,
    configId: string,
    update: ScoreConfigUpdate,
  ): Promise<ScoreConfig> =>
    api.patch<ScoreConfig>(`${configsPath(projectId)}/${encodeURIComponent(configId)}`, update),
  /** `409 SCORE_CONFIG_IN_USE` while scores carry the config's name. */
  deleteConfig: (projectId: string, configId: string): Promise<void> =>
    api.delete(`${configsPath(projectId)}/${encodeURIComponent(configId)}`),

  /** Every score of the trace, newest first. */
  forTrace: (projectId: string, traceId: string): Promise<Score[]> =>
    api.get<Score[]>(traceScoresPath(projectId, traceId)),
  /** A human score (201). */
  create: (projectId: string, traceId: string, input: ScoreCreate): Promise<Score> =>
    api.post<Score>(traceScoresPath(projectId, traceId), input),
  delete: (projectId: string, scoreId: string): Promise<void> =>
    api.delete(`${projectPath(projectId)}/scores/${encodeURIComponent(scoreId)}`),
};
