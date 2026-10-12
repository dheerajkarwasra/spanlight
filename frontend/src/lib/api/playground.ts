import { api } from "./client";
import { projectPath } from "./paths";
import type { ChatMessage } from "./prompts";

/** Run a saved prompt version, filling its `{{variables}}`. */
export interface PromptSource {
  prompt_version_id: string;
  variables?: Record<string, string>;
}

/** Run messages written in place. */
export interface MessagesSource {
  messages: ChatMessage[];
}

export interface PlaygroundParams {
  temperature?: number | null;
  max_tokens?: number | null;
  top_p?: number | null;
}

/** One side of the comparison. */
export interface PlaygroundConfig {
  credential_id: string;
  model: string;
  params?: PlaygroundParams;
  source: PromptSource | MessagesSource;
}

export interface PlaygroundUsage {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number | null;
}

/**
 * One result per configuration, in order. A configuration that failed has its `error` (a
 * provider error, a missing variable, `BUDGET_EXCEEDED`) and null everywhere else; the others are
 * unaffected. Unknown cost or usage is null, never zero.
 */
export interface PlaygroundResult {
  output: string | null;
  usage: PlaygroundUsage | null;
  cost_usd: string | null;
  latency_ms: number | null;
  /** Always null: runs are not streamed. */
  ttft_ms: number | null;
  trace_id: string | null;
  span_id: string | null;
  error: string | null;
}

export interface PlaygroundRun {
  results: PlaygroundResult[];
}

/**
 * The playground under `/projects/{id}/playground`. Needs `experiments:run` (member). Every run
 * is a real provider call that shows up as a trace in the `playground` environment. Errors:
 * `409 NOT_CONFIGURED`, `404 NOT_FOUND` (credential or version), `429 RATE_LIMITED`.
 */
export const playgroundApi = {
  /** 1 to 3 configurations side by side. Pass an `Idempotency-Key` so a retry does not spend twice. */
  run: (
    projectId: string,
    configs: PlaygroundConfig[],
    idempotencyKey?: string,
  ): Promise<PlaygroundRun> =>
    api.post<PlaygroundRun>(
      `${projectPath(projectId)}/playground/runs`,
      { configs },
      idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    ),
};
