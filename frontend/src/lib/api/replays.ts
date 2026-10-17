import { api } from "./client";
import { projectPath } from "./paths";
import type { PlaygroundUsage } from "./playground";
import type { Page } from "./types";

/**
 * `exact`: the stored request as it was sent; `modified`: sent with stated changes (stream
 * removed, max_tokens capped, model changed); `messages_only`: an OTLP span recorded without its
 * request parameters.
 */
export type ReplayFidelity = "exact" | "modified" | "messages_only";

export type ReplaySurface = "chat_completions" | "responses" | "messages" | "models";

/** The credential family a replay needs: `openai` (any OpenAI-compatible server) or `anthropic`. */
export type ReplayProviderKind = "openai" | "anthropic";

export type ReplayStatus = "running" | "done" | "failed";

/** One row of the replay ledger. */
export interface ReplaySummary {
  replay_id: string;
  /** Null once the user was deleted. */
  requested_by: string | null;
  model: string;
  fidelity: ReplayFidelity;
  status: ReplayStatus;
  /** `budget_exceeded`, `abandoned`, `replay_failed`, `BUDGET_EXCEEDED` or the provider's error. */
  error: string | null;
  /** What the replay was charged once settled (0 while running). */
  cost_usd: string;
  started_at: string;
  finished_at: string | null;
  /** The replayed call's span; null while running or when it was not stored. */
  result_trace_id: string | null;
  result_span_id: string | null;
}

/** Why a span cannot be replayed as it was: a `REPLAY_*` problem code and its explanation. */
export interface ReplayRefusal {
  code: string;
  detail: string;
}

/**
 * What a replay of the span would send, or why it is refused (then `fidelity`, `surface`,
 * `provider_kind` and `body` are null), and its last ten replays.
 */
export interface ReplaySource {
  fidelity: ReplayFidelity | null;
  surface: ReplaySurface | null;
  provider_kind: ReplayProviderKind | null;
  /** The stored, already-redacted request. */
  body: Record<string, unknown> | null;
  refusal: ReplayRefusal | null;
  replays: ReplaySummary[];
}

/** The body of `POST /playground/replays`. */
export interface ReplayInput {
  trace_id: string;
  span_id: string;
  /** A credential of the organization of the span's provider kind. */
  credential_id: string;
  /** Another model of the same provider; default the stored one. */
  model?: string | null;
  /** 1 to 4,096; default the stored value, capped. */
  max_tokens?: number | null;
  /** Replaces the stored request as a whole; at most 256 KB. */
  edited_body?: Record<string, unknown> | null;
}

/**
 * One finished replay. `error` set means no `output`; `usage`, `cost_usd` and `latency_ms` follow
 * the playground's result (unknown is null, never zero).
 */
export interface ReplayResult {
  replay_id: string;
  fidelity: ReplayFidelity;
  /** The request fields changed from the stored ones, e.g. `max_tokens`, `model`, `body`. */
  changed: string[];
  /** Whether the replayed request hashes the same as the stored one; null when not comparable. */
  same_request_hash: boolean | null;
  source: {
    trace_id: string;
    span_id: string;
    /** The answer the source span recorded. */
    output_text: string | null;
  };
  output: string | null;
  usage: PlaygroundUsage | null;
  cost_usd: string | null;
  latency_ms: number | null;
  /** Always null: replays are not streamed. */
  ttft_ms: number | null;
  /** The replayed call's own trace and span. */
  trace_id: string | null;
  span_id: string | null;
  /** `BUDGET_EXCEEDED` (a project budget blocked the call), `replay_failed`, or the provider's. */
  error: string | null;
}

/** The project's replay budget for the current UTC month; money as decimal strings. */
export interface ReplayBudget {
  monthly_budget_usd: string;
  /** What settled replays were charged this month. */
  spent_usd: string;
  /** What running replays hold until they settle. */
  reserved_usd: string;
  /** The budget minus both, never below zero. */
  remaining_usd: string;
  month_start: string;
}

export interface ReplayListQuery {
  trace_id?: string | undefined;
  span_id?: string | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

function spanPath(projectId: string, traceId: string, spanId: string): string {
  const trace = `${projectPath(projectId)}/traces/${encodeURIComponent(traceId)}`;
  return `${trace}/spans/${encodeURIComponent(spanId)}`;
}

function replaysPath(projectId: string): string {
  return `${projectPath(projectId)}/playground/replays`;
}

/**
 * Exact replays of stored LLM requests. Reads need `project:read`; a replay needs
 * `experiments:run` (member) and is a real provider call charged to the project's monthly replay
 * budget. Errors: `404 NOT_FOUND` (span or credential), `409 NOT_CONFIGURED`,
 * `409 REPLAY_BUDGET_EXCEEDED` (with `spent_usd`, `reserved_usd`, `budget_usd`, `month_start`),
 * `422 REPLAY_*` refusals, `429 RATE_LIMITED`.
 */
export const replaysApi = {
  /** How the span would be replayed, or why it cannot be; no provider is called. */
  source: (projectId: string, traceId: string, spanId: string): Promise<ReplaySource> =>
    api.get<ReplaySource>(`${spanPath(projectId, traceId, spanId)}/replay-source`),
  /** Sends the stored request once. Pass an `Idempotency-Key` so a retry does not spend twice. */
  run: (projectId: string, input: ReplayInput, idempotencyKey: string): Promise<ReplayResult> =>
    api.post<ReplayResult>(replaysPath(projectId), input, { "Idempotency-Key": idempotencyKey }),
  /** Newest first; `trace_id` and `span_id` narrow to one source span. */
  list: (projectId: string, query: ReplayListQuery = {}): Promise<Page<ReplaySummary>> =>
    api.get<Page<ReplaySummary>>(replaysPath(projectId), { ...query }),
  budget: (projectId: string): Promise<ReplayBudget> =>
    api.get<ReplayBudget>(`${replaysPath(projectId)}/budget`),
};
