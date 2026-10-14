import type { ExperimentRun, ExperimentTarget, PromptListItem } from "@/lib/api";
import { formatPromptVersion, formatRelativeTime } from "@/lib/format";

/** The prompt name and version number a prompt version id stands for. */
export interface PromptRef {
  name: string;
  version: number;
}

/** Prompt version id → its prompt and number, read by id for the targets on screen. */
export type VersionIndex = ReadonlyMap<string, PromptRef>;

/** `ended` is a failed run whose reason is not known here (an experiment's `last_run`). */
export type RunState = "queued" | "running" | "done" | "stopped" | "failed" | "ended";

/** A run that ended (done or failed) can be compared; a queued or running one cannot yet. */
export function isFinished(run: Pick<ExperimentRun, "status">): boolean {
  return run.status === "done" || run.status === "failed";
}

export function isActive(run: Pick<ExperimentRun, "status">): boolean {
  return run.status === "queued" || run.status === "running";
}

/** How a run reads: a run stopped by its cost limit is a warning, not a failure. */
export function runState(run: { status: string; error?: string | null }): RunState {
  if (run.status === "failed") {
    if (run.error === undefined) {
      return "ended";
    }
    return run.error === "cost_cap_reached" ? "stopped" : "failed";
  }
  if (run.status === "queued" || run.status === "running" || run.status === "done") {
    return run.status;
  }
  return "failed";
}

/** "Run #3": the run's stable number, "Run" while it is not known yet. */
export function runName(number: number | undefined): string {
  return number === undefined ? "Run" : `Run #${number}`;
}

/** "support/answer v2" for a prompt target, "Ad-hoc messages" for typed messages. */
export function targetLabel(target: ExperimentTarget, index: VersionIndex): string {
  if (target.kind === "messages") {
    return "Ad-hoc messages";
  }
  const ref = index.get(target.prompt_version_id);
  return ref ? formatPromptVersion(ref.name, ref.version) : "Prompt version";
}

/** "Run #2 · support/answer v1 · 2 hours ago", as the compare pickers read. */
export function runOptionLabel(
  run: ExperimentRun,
  index: VersionIndex,
  now: Date = new Date(),
): string {
  const when = formatRelativeTime(run.started_at, now);
  return [runName(run.number), targetLabel(run.target, index), when].filter(Boolean).join(" · ");
}

/** Plain words for why a run ended early. Never a raw code. */
export function runEndReason(error: string | null): string {
  switch (error) {
    case null:
      return "";
    case "cost_cap_reached":
      return "Stopped: cost limit reached";
    case "abandoned":
      return "Stopped: the worker stopped reporting progress";
    case "cancelled":
      return "Cancelled before it finished";
    case "model_unpriced":
      return "Not started: the model has no price, so the cost limit can't be enforced";
    case "credential_missing":
      return "Not started: the credential was deleted";
    case "credentials_not_configured":
      return "Not started: the worker has no CREDENTIALS_KEYS. Set it on the worker and run again.";
    case "prompt_version_missing":
      return "Not started: the prompt version was deleted";
    case "experiment_config_invalid":
      return "Not started: the experiment's settings are no longer valid";
    case "lease_lost":
      return "Stopped: the worker lost the job";
    default:
      return "Failed: something went wrong running this experiment";
  }
}

/** Elapsed time as "4 min 12 s", "2 min 10 s" or "38 s". */
export function formatElapsed(ms: number | null): string | null {
  if (ms === null || !Number.isFinite(ms) || ms < 0) {
    return null;
  }
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes === 0) {
    return `${seconds} s`;
  }
  if (minutes < 60) {
    return `${minutes} min ${seconds} s`;
  }
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** How long a run took (or has taken so far, while it runs). */
export function runDurationMs(run: ExperimentRun, now: Date = new Date()): number | null {
  const started = Date.parse(run.started_at);
  const ended = run.finished_at ? Date.parse(run.finished_at) : isActive(run) ? now.getTime() : NaN;
  return Number.isNaN(started) || Number.isNaN(ended) ? null : ended - started;
}

/** The ticked ids that still name a finished run of this list (stale ones are dropped). */
export function liveTicks(runs: readonly ExperimentRun[], ticked: readonly string[]): string[] {
  return ticked.filter((id) => runs.some((run) => run.id === id && isFinished(run)));
}

/**
 * The pair to compare, lower number as A: the two ticked runs when both still exist and ended,
 * else the two newest finished ones.
 */
export function comparePair(
  runs: readonly ExperimentRun[],
  ticked: readonly string[],
): [string, string] | null {
  const valid = liveTicks(runs, ticked);
  const chosen =
    valid.length === 2
      ? runs.filter((run) => valid.includes(run.id))
      : runs.filter((run) => isFinished(run)).slice(0, 2);
  const [first, second] = [...chosen].sort((x, y) => x.number - y.number);
  return first && second ? [first.id, second.id] : null;
}

/** "v2 · production", "v3 · latest": a version with the labels that point at it. */
export function versionOptionLabel(version: number, prompt: PromptListItem | undefined): string {
  const labels = (prompt?.labels ?? []).filter((label) => label.version === version);
  const names = labels.map((label) => label.label);
  if (prompt?.latest_version === version) {
    names.push("latest");
  }
  return names.length > 0 ? `v${version} · ${names.join(", ")}` : `v${version}`;
}
