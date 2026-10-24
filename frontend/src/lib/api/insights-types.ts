/**
 * Doctor types, mirroring the backend's insight bodies exactly (snake_case; metric values and
 * money as decimal strings; every unknown value `null`). Re-exported through `types.ts`.
 */

export type InsightSeverity = "info" | "warning" | "critical";

export type InsightStatus = "open" | "acknowledged" | "resolved" | "muted";

/** Where a problem lives. */
export type FailureLayer = "client" | "request" | "agent" | "provider" | "platform" | "traffic";

/** `measured` restates numbers; `inferred` is a pattern that suggests the cause. */
export type InsightCertainty = "measured" | "inferred";

export interface InsightEvidence {
  /** At most 20 traces that show the problem. */
  trace_ids: string[];
  /** Decimal strings for rates and money, integers for counts, `null` when unknown. */
  metrics: Record<string, string | number | null>;
  window: { start: string; end: string };
}

export interface Insight {
  id: string;
  /** The detector's kind, e.g. `error_spike`. */
  kind: string;
  /** The catalogue's name for the kind (the kind itself when unknown to the server). */
  label: string;
  severity: InsightSeverity;
  status: InsightStatus;
  title: string;
  summary: string;
  failure_layer: FailureLayer;
  certainty: InsightCertainty;
  evidence: InsightEvidence;
  suggested_fix: string;
  verification: string;
  first_seen_at: string;
  last_seen_at: string;
  occurrences: number;
  resolved_at: string | null;
  muted_until: string | null;
  mute_reason: string | null;
  acknowledged_at: string | null;
}

/** Claude's advisory explanation of an insight; plain text. */
export interface Explanation {
  id: string;
  model: string;
  text: string;
  /** From the call's token usage; `null` when unknown. */
  cost_usd: string | null;
  created_by: string | null;
  created_at: string;
}

/** A member's verdict on whether an insight was right. */
export type FeedbackVerdict = "right" | "wrong";

/** The body of a vote. `note` is at most 500 characters. */
export interface FeedbackInput {
  verdict: FeedbackVerdict;
  note?: string | undefined;
}

/** The caller's stored vote, as `PUT .../feedback` answers it. */
export interface Feedback {
  verdict: FeedbackVerdict;
  note: string | null;
  updated_at: string;
}

/** The votes on one insight: the caller's own, and everyone's counts. */
export interface InsightFeedback {
  /** Null until the caller votes; `updated_at` is when the vote was last given. */
  mine: { verdict: FeedbackVerdict; note: string | null; updated_at: string } | null;
  right: number;
  wrong: number;
}

/**
 * The span an evidence trace opens on: its first failed span (LLM preferred), else its first LLM
 * span, else its first span. `status` is the span's stored status (`ok`, `error`, `unset`).
 */
export interface EvidenceSpan {
  trace_id: string;
  span_id: string;
  name: string;
  status: string;
  model: string | null;
}

/** The insight and its completed explanations, newest first (at most 20). */
export interface InsightDetail extends Insight {
  explanations: Explanation[];
  /** One per evidence trace still stored, in `evidence.trace_ids` order. */
  evidence_spans: EvidenceSpan[];
  feedback: InsightFeedback;
}

/**
 * Doctor precision for one detector kind, from member feedback. Decimals are strings, `null` when
 * `n` is 0. `meets_target`: precision at least 0.8, Wilson lower bound at least 0.6, `n` at least
 * 20, computed on the server.
 */
export interface PrecisionKind {
  kind: string;
  /** The catalogue's name for the kind (the kind itself when unknown to the server). */
  label: string;
  right: number;
  wrong: number;
  /** `right + wrong`. */
  n: number;
  precision: string | null;
  wilson_low: string | null;
  wilson_high: string | null;
  meets_target: boolean;
}

export interface PrecisionOut {
  kinds: PrecisionKind[];
}

/** On the votes' `updated_at`; all time when both are left out. */
export interface PrecisionQuery {
  from?: string | undefined;
  to?: string | undefined;
}

/** Insights that still need a person (open and acknowledged), by severity. */
export interface InsightSummary {
  open_critical: number;
  open_warning: number;
  open_info: number;
}

export interface InsightListQuery {
  /** Repeatable on the wire; omit for every status. */
  status?: readonly InsightStatus[] | undefined;
  severity?: InsightSeverity | undefined;
  kind?: string | undefined;
  /** Keeps insights that cite the trace. */
  trace_id?: string | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

/** `until` is an ISO time with an offset, in the future and at most 90 days ahead. */
export interface MuteInput {
  until: string;
  /** 1 to 500 characters. */
  reason: string;
}

export interface HealthComponent {
  /** `findings`, `errors`, `latency` or `cost`. */
  name: string;
  /** Points taken off the score. */
  penalty: number;
  observed: string | null;
  baseline: string | null;
}

export interface Health {
  /** 0 to 100; `null` (never 0) when the window had no LLM calls. */
  value: number | null;
  components: HealthComponent[];
  open_critical: number;
  open_warning: number;
  window: { start: string; end: string };
  /** A percentile behind the score was estimated from rollup histograms. */
  approximate: boolean;
}

export interface HealthQuery {
  from?: string | undefined;
  to?: string | undefined;
  environment?: string | undefined;
}

export interface DetectorRun {
  id: string;
  detector: string;
  window_start: string;
  window_end: string;
  /** `null` when the run failed (`error` is set). */
  findings: number | null;
  truncated: boolean;
  duration_ms: number;
  error: string | null;
  ran_at: string;
}
