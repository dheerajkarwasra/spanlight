import { api } from "./client";
import { projectPath } from "./paths";
import type { ChatMessage } from "./prompts";
import type { Page } from "./types";

/** Sampling parameters; an unset one is left to the provider's default. */
export interface ExperimentParams {
  temperature?: number | null;
  /** At most 4,096. */
  max_tokens?: number | null;
  top_p?: number | null;
}

/** A stored prompt version; each dataset item's input fills its variables. */
export interface PromptVersionTarget {
  kind: "prompt_version";
  prompt_version_id: string;
  credential_id: string;
  model: string;
  params?: ExperimentParams;
}

/** Typed messages, sent unchanged for every item (the item's input is not used). */
export interface AdHocTarget {
  kind: "messages";
  credential_id: string;
  model: string;
  messages: ChatMessage[];
  params?: ExperimentParams;
}

export type ExperimentTarget = PromptVersionTarget | AdHocTarget;

export interface ExperimentConfig {
  target: ExperimentTarget;
  /** A decimal string in responses; the run stops before it could pass this. Default 5. */
  max_cost_usd: string;
  /** Default and ceiling 1,000 (the server's `EXPERIMENT_MAX_ITEMS`). */
  max_items: number;
}

/** An experiment's latest run, for its list row. It carries no `error`. */
export interface LastRun {
  id: string;
  number: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  /** Stored when the run ended; null while it is unfinished. */
  totals: RunTotals | null;
}

export interface Experiment {
  id: string;
  name: string;
  dataset_id: string;
  config: ExperimentConfig;
  evaluator_ids: string[];
  created_by: string | null;
  created_at: string;
  run_count: number;
  /** The run with the highest number; null before the first run. */
  last_run: LastRun | null;
}

export interface ExperimentCreate {
  name: string;
  dataset_id: string;
  config: {
    target: ExperimentTarget;
    max_cost_usd?: number | string;
    max_items?: number;
  };
  evaluator_ids?: string[];
}

export type ExperimentRunStatus = "queued" | "running" | "done" | "failed";

export interface ScoreStat {
  /** The mean of a numeric score (booleans count 1 or 0); null for a categorical one. */
  mean: number | null;
  count: number;
  /** Items per category of a categorical score; null for a numeric one. */
  categories: Record<string, number> | null;
  /**
   * Boolean scores only: the items that passed and the Wilson 95% interval on the pass rate
   * (4-place decimal strings). Null for numeric and categorical scores, with no scored item, and
   * on totals stored before intervals existed.
   */
  passed: number | null;
  ci_low: string | null;
  ci_high: string | null;
}

export interface RunTotals {
  items: number;
  /** Items whose call failed. */
  errors: number;
  /** Answered items whose scoring broke (`evaluator_error` set); never overlaps `errors`. */
  evaluator_errors: number;
  /** The sum of the known item costs; null when no item's cost is known. */
  cost_usd: string | null;
  /** An item that made a call has no known cost: `cost_usd` is then a lower bound. */
  has_unpriced: boolean;
  /** Nearest-rank percentiles over the items that answered; null without any. */
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  scores: Record<string, ScoreStat>;
}

export interface ExperimentRun {
  id: string;
  experiment_id: string;
  /** 1-based and stable: "Run #3" is always the third run of the experiment. */
  number: number;
  /** The target this run used: the experiment's, or its prompt at an overriding version. */
  target: ExperimentTarget;
  /** Usually an `ExperimentRunStatus`. */
  status: string;
  /** Why a run failed: `cost_cap_reached`, `abandoned`, `cancelled`, `model_unpriced`, … */
  error: string | null;
  started_at: string;
  finished_at: string | null;
  heartbeat_at: string | null;
  started_by: string | null;
  /**
   * A single run's read computes them live while it runs; lists and the start answer carry the
   * stored totals, null until the run ended.
   */
  totals: RunTotals | null;
}

/** One score of an item: a number, a boolean or a category. */
export type ItemScore = number | boolean | string;

export interface ExperimentItem {
  dataset_item_id: string;
  /** Null when the call never reached the gateway or its span was not stored. */
  trace_id: string | null;
  /** `{text, finish_reason}`; null when the item failed. */
  output: { text?: string | null; finish_reason?: string | null } | null;
  /** An evaluator that did not score the item is absent. */
  scores: Record<string, ItemScore>;
  /** The judge's reasoning per score name; null without a trace or any comment. */
  score_comments: Record<string, string> | null;
  latency_ms: number | null;
  cost_usd: string | null;
  /** `missing_variables: a, b`, `BUDGET_EXCEEDED` or the provider's failure. */
  error: string | null;
  /** Scoring broke (the answer and its cost are kept); its scores are then absent. */
  evaluator_error: string | null;
  /** The run's copy of the dataset item; all three are null on runs made before copies were kept. */
  input: unknown;
  expected_output: unknown;
  item_metadata: Record<string, unknown> | null;
  /** The item's 1-based place in the run. */
  position: number;
  created_at: string;
}

export interface CompareItemSide {
  trace_id: string | null;
  output: { text?: string | null; finish_reason?: string | null } | null;
  scores: Record<string, ItemScore>;
  latency_ms: number | null;
  cost_usd: string | null;
  error: string | null;
}

export interface ItemDelta {
  dataset_item_id: string;
  /** The item's 1-based place in run a, else in run b. */
  position: number;
  /** Run a's copy of the item, else run b's; null when neither run kept one. */
  input: unknown;
  expected_output: unknown;
  /**
   * True when the item was edited between the runs (the pair is then left out of the aggregate);
   * null when either run kept no copy.
   */
  changed: boolean | null;
  /** Null when the item is not in run a (or b). */
  a: CompareItemSide | null;
  b: CompareItemSide | null;
  /** `b - a` per numeric score; null for a categorical score or an item in one run only. */
  deltas: Record<string, number | null>;
}

export interface ScoreDelta {
  /** Means over each run's scored items; null for a categorical score. Higher is better. */
  mean_a: number | null;
  mean_b: number | null;
  delta: number | null;
  /** Items in both runs that rose, fell or stayed the same. */
  improved: number;
  regressed: number;
  unchanged: number;
  /** Boolean scores only: each run's Wilson 95% interval on its pass rate. */
  ci_a: ScoreInterval | null;
  ci_b: ScoreInterval | null;
  /** Null for a categorical score, or when fewer than two matched items carry the score. */
  test: PairedTest | null;
}

/** A 95% interval on a rate, as 4-place decimal strings in [0, 1]. */
export interface ScoreInterval {
  low: string;
  high: string;
}

/** The paired test of two runs on the same items: exact McNemar (boolean) or the sign test. */
export interface PairedTest {
  kind: "mcnemar" | "sign";
  /** Matched items that changed (`improved + regressed`). */
  n: number;
  improved: number;
  regressed: number;
  unchanged: number;
  /** Exact two-sided p-value, a 4-place decimal string. */
  p_value: string;
  /** Decided at alpha 0.05 on the exact value. */
  significant: boolean;
}

export interface RunComparison {
  /** Items in both runs first, then those only in a, then those only in b. */
  items: ItemDelta[];
  aggregate: Record<string, ScoreDelta>;
  /** `b - a`; null when either side is unknown. */
  cost_delta_usd: string | null;
  latency_p95_delta_ms: number | null;
  /** Pairs whose item was edited between the runs; they are left out of the aggregate. */
  changed_items: number;
}

export interface ExperimentListQuery {
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

function experimentsPath(projectId: string): string {
  return `${projectPath(projectId)}/experiments`;
}

function experimentPath(projectId: string, experimentId: string): string {
  return `${experimentsPath(projectId)}/${encodeURIComponent(experimentId)}`;
}

function runPath(projectId: string, experimentId: string, runId: string): string {
  return `${experimentPath(projectId, experimentId)}/runs/${encodeURIComponent(runId)}`;
}

/**
 * Experiments under `/projects/{id}/experiments`. Reads need `project:read`; creating and
 * running need `experiments:run` (member), because a run makes real provider calls. Errors:
 * `409 NAME_TAKEN`, `409 RUN_IN_PROGRESS`, `422 EXPERIMENT_MODEL_UNPRICED`,
 * `422 TARGET_OVERRIDE_INVALID`, `404` for a dataset, evaluator, prompt version or credential.
 */
export const experimentsApi = {
  /** Newest first. */
  list: (projectId: string, query: ExperimentListQuery = {}): Promise<Page<Experiment>> =>
    api.get<Page<Experiment>>(experimentsPath(projectId), { ...query }),
  get: (projectId: string, experimentId: string): Promise<Experiment> =>
    api.get<Experiment>(experimentPath(projectId, experimentId)),
  create: (projectId: string, input: ExperimentCreate): Promise<Experiment> =>
    api.post<Experiment>(experimentsPath(projectId), input),

  /**
   * Queues a run (202). `promptVersionId` runs a prompt-version experiment with another version
   * of the same prompt. Pass an `Idempotency-Key` so a retry does not start a second run.
   */
  startRun: (
    projectId: string,
    experimentId: string,
    idempotencyKey: string,
    promptVersionId?: string | null,
  ): Promise<ExperimentRun> =>
    api.post<ExperimentRun>(
      `${experimentPath(projectId, experimentId)}/runs`,
      promptVersionId ? { target_override: { prompt_version_id: promptVersionId } } : {},
      { "Idempotency-Key": idempotencyKey },
    ),
  /** Newest first, without totals. */
  runs: (
    projectId: string,
    experimentId: string,
    query: ExperimentListQuery = {},
  ): Promise<Page<ExperimentRun>> =>
    api.get<Page<ExperimentRun>>(`${experimentPath(projectId, experimentId)}/runs`, {
      ...query,
    }),
  /** One run with its totals, computed from its items while it runs. */
  run: (projectId: string, experimentId: string, runId: string): Promise<ExperimentRun> =>
    api.get<ExperimentRun>(runPath(projectId, experimentId, runId)),
  /** In dataset item order. */
  items: (
    projectId: string,
    experimentId: string,
    runId: string,
    query: ExperimentListQuery = {},
  ): Promise<Page<ExperimentItem>> =>
    api.get<Page<ExperimentItem>>(`${runPath(projectId, experimentId, runId)}/items`, {
      ...query,
    }),
  /** Deltas are `b - a`. `409 RUN_IN_PROGRESS` while either run is queued or running. */
  compare: (
    projectId: string,
    experimentId: string,
    runA: string,
    runB: string,
  ): Promise<RunComparison> =>
    api.get<RunComparison>(
      `${runPath(projectId, experimentId, runA)}/compare/${encodeURIComponent(runB)}`,
    ),
};
