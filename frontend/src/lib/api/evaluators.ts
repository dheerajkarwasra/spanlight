import { api } from "./client";
import { projectPath } from "./paths";
import type { Page } from "./types";

/** Where a heuristic rule looks. */
export type RuleTarget = "output" | "input";

export type HeuristicCheck =
  | { rule: "regex"; pattern: string; target?: RuleTarget; ignore_case?: boolean }
  | { rule: "json_valid"; target?: RuleTarget }
  /** The score is the length itself (numeric); the rule has no bounds. */
  | { rule: "length"; target?: RuleTarget; unit?: "chars" | "words" }
  | { rule: "contains"; needle: string; target?: RuleTarget; case_sensitive?: boolean }
  | { rule: "exact_match"; normalize_whitespace?: boolean; case_sensitive?: boolean }
  | { rule: "levenshtein" };

export interface HeuristicConfig {
  kind: "heuristic";
  check: HeuristicCheck;
}

/** `temperature` is a decimal string (or null: not sent) in responses and a number or string in requests. */
export interface JudgeConfig {
  kind: "llm_judge";
  prompt_version_id: string;
  credential_id: string;
  model: string;
  max_tokens?: number;
  temperature?: number | string | null;
}

export interface WebhookConfig {
  kind: "webhook";
  channel_id: string;
}

export type EvaluatorConfig = HeuristicConfig | JudgeConfig | WebhookConfig;
export type EvaluatorKind = EvaluatorConfig["kind"];

/** The built-in judge prompts; the server stores one as the project's prompt `judges/<name>`. */
export type JudgeTemplateName = "relevance" | "faithfulness" | "toxicity" | "format_compliance";

/**
 * A judge config as a request sends it: exactly one of `prompt_version_id` and
 * `judge_template`. A stored judge always names a prompt version (`JudgeConfig`).
 */
export type JudgeConfigInput = Omit<JudgeConfig, "prompt_version_id"> &
  (
    | { prompt_version_id: string; judge_template?: never }
    | { judge_template: JudgeTemplateName; prompt_version_id?: never }
  );

export type EvaluatorConfigInput = HeuristicConfig | JudgeConfigInput | WebhookConfig;

/** Which traces an evaluator looks at; every field narrows, an empty list matches all. */
export interface EvaluatorFilters {
  environments?: string[];
  models?: string[];
  tags?: string[];
  trace_names?: string[];
  min_duration_ms?: number | null;
}

export interface Evaluator {
  id: string;
  name: string;
  kind: EvaluatorKind;
  config: EvaluatorConfig;
  /** A decimal string between "0" and "1". */
  sampling_rate: string;
  filters: EvaluatorFilters;
  /** Monthly cap in USD as a decimal string; null for none. A judge always has one. */
  monthly_budget_usd: string | null;
  /** The month so far (UTC); null for an evaluator without a budget. */
  budget: EvaluatorBudgetSummary | null;
  enabled: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

/** Decimal strings. `reserved_usd` is held by running judge calls. */
export interface EvaluatorBudgetSummary {
  monthly_budget_usd: string;
  spent_usd: string;
  reserved_usd: string;
}

export interface EvaluatorCreate {
  name: string;
  config: EvaluatorConfigInput;
  sampling_rate?: number | string;
  filters?: EvaluatorFilters;
  monthly_budget_usd?: number | string | null;
  enabled?: boolean;
}

export interface EvaluatorUpdate {
  name?: string | null;
  enabled?: boolean | null;
  sampling_rate?: number | string | null;
  filters?: EvaluatorFilters | null;
  monthly_budget_usd?: number | string | null;
  /** Merged into the stored config; its `kind` cannot change. A `judge_template` replaces the
   * stored `prompt_version_id`. */
  config?: Record<string, unknown> | null;
}

export type EvaluatorRunStatus = "queued" | "running" | "done" | "failed";
/** Why a run happened: sampled live, a test run, or an experiment item. */
export type EvaluatorRunSource = "online" | "test" | "experiment";

/** A score a run wrote: `value` (a JSON number, like every score) for numeric and boolean (1/0)
 * scores, `string_value` for categorical ones. */
export interface EvaluatorRunScore {
  name: string;
  value: number | null;
  string_value: string | null;
}

export interface EvaluatorRun {
  id: string;
  trace_id: string;
  span_id: string | null;
  /** A run the budget stopped is `failed` with `error: "budget_exceeded"`. */
  status: EvaluatorRunStatus;
  source: EvaluatorRunSource;
  cost_usd: string;
  reserved_usd: string;
  /** Why the run ended without a score, e.g. `budget_exceeded`; null when it worked. */
  error: string | null;
  started_at: string;
  heartbeat_at: string | null;
  finished_at: string | null;
  /** Empty until the run is done. */
  scores: EvaluatorRunScore[];
}

export interface EvaluatorBudget {
  /** Null for an evaluator without a cap. */
  monthly_budget_usd: string | null;
  spent_usd: string;
  reserved_usd: string;
  remaining_usd: string | null;
  month_start: string;
}

export interface EvaluatorTestScore {
  name: string;
  span_id: string | null;
  /** A JSON number, like every score; null for a categorical score. */
  value: number | null;
  string_value: string | null;
  comment: string | null;
}

/** A dry run on one trace: no score is stored, but a judge's cost is real and budgeted. */
export interface EvaluatorTestResult {
  scores: EvaluatorTestScore[];
  cost_usd: string;
  reasoning: string | null;
}

export interface EvaluatorListQuery {
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

export interface EvaluatorRunListQuery {
  status?: EvaluatorRunStatus | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

function evaluatorsPath(projectId: string): string {
  return `${projectPath(projectId)}/evaluators`;
}

function evaluatorPath(projectId: string, evaluatorId: string): string {
  return `${evaluatorsPath(projectId)}/${encodeURIComponent(evaluatorId)}`;
}

/**
 * Evaluators under `/projects/{id}/evaluators`. Reads need `project:read`; writes and the test
 * run need `evaluators:write` (admin), because a judge spends money unattended. Create and
 * update errors: `409 NAME_TAKEN`, `404` for a prompt version, credential or channel the project
 * lacks, `422 BUDGET_REQUIRED`, `422 RULE_NEEDS_EXPECTED_OUTPUT`, `422 CHANNEL_KIND`,
 * `422 JUDGE_TEMPLATE_VARIABLES`, `422 EVALUATOR_MODEL_UNPRICED`, `422 VALIDATION_ERROR`.
 */
export const evaluatorsApi = {
  list: (projectId: string, query: EvaluatorListQuery = {}): Promise<Page<Evaluator>> =>
    api.get<Page<Evaluator>>(evaluatorsPath(projectId), { ...query }),
  get: (projectId: string, evaluatorId: string): Promise<Evaluator> =>
    api.get<Evaluator>(evaluatorPath(projectId, evaluatorId)),
  create: (projectId: string, input: EvaluatorCreate): Promise<Evaluator> =>
    api.post<Evaluator>(evaluatorsPath(projectId), input),
  update: (projectId: string, evaluatorId: string, update: EvaluatorUpdate): Promise<Evaluator> =>
    api.patch<Evaluator>(evaluatorPath(projectId, evaluatorId), update),
  delete: (projectId: string, evaluatorId: string): Promise<void> =>
    api.delete(evaluatorPath(projectId, evaluatorId)),

  /** Newest first. Keyset-paginated. */
  runs: (
    projectId: string,
    evaluatorId: string,
    query: EvaluatorRunListQuery = {},
  ): Promise<Page<EvaluatorRun>> =>
    api.get<Page<EvaluatorRun>>(`${evaluatorPath(projectId, evaluatorId)}/runs`, { ...query }),
  /** This month's spend against the cap. */
  budget: (projectId: string, evaluatorId: string): Promise<EvaluatorBudget> =>
    api.get<EvaluatorBudget>(`${evaluatorPath(projectId, evaluatorId)}/budget`),
  /** `404` unknown trace, `422 VALIDATION_ERROR` nothing to evaluate, `409 NOT_CONFIGURED`,
   * `402 EVALUATOR_BUDGET_EXCEEDED` (no call made), `502 EVALUATOR_FAILED` (`error` names why). */
  test: (projectId: string, evaluatorId: string, traceId: string): Promise<EvaluatorTestResult> =>
    api.post<EvaluatorTestResult>(`${evaluatorPath(projectId, evaluatorId)}/test`, {
      trace_id: traceId,
    }),
};
