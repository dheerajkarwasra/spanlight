import { api } from "./client";
import { projectPath } from "./paths";
import type {
  DetectorRun,
  Explanation,
  Feedback,
  FeedbackInput,
  Health,
  HealthQuery,
  Insight,
  InsightDetail,
  InsightListQuery,
  InsightSummary,
  MuteInput,
  Page,
  PrecisionOut,
  PrecisionQuery,
} from "./types";

function insightsPath(projectId: string): string {
  return `${projectPath(projectId)}/insights`;
}

function insightPath(projectId: string, insightId: string): string {
  return `${insightsPath(projectId)}/${encodeURIComponent(insightId)}`;
}

/**
 * The Doctor's insights. Reads need `project:read`; acting on an insight and asking for an
 * explanation need `insights:manage` (admin). Errors: `422 INVALID_MUTE`, `409
 * INVALID_TRANSITION`, `409 NOT_CONFIGURED`, `409 EXPLAIN_MODEL_UNPRICED`, `402
 * EXPLAIN_BUDGET_EXCEEDED`, `502 EXPLAIN_FAILED`.
 */
export const insightsApi = {
  /** Most recently seen first. Keyset-paginated; `status` repeats on the wire. */
  list: (projectId: string, query: InsightListQuery = {}): Promise<Page<Insight>> =>
    api.get<Page<Insight>>(insightsPath(projectId), { ...query }),
  /** Open and acknowledged insights by severity; drives the navigation badge. */
  summary: (projectId: string): Promise<InsightSummary> =>
    api.get<InsightSummary>(`${insightsPath(projectId)}/summary`),
  get: (projectId: string, insightId: string): Promise<InsightDetail> =>
    api.get<InsightDetail>(insightPath(projectId, insightId)),
  /** `409 INVALID_TRANSITION` unless the insight is open. */
  acknowledge: (projectId: string, insightId: string): Promise<Insight> =>
    api.post<Insight>(`${insightPath(projectId, insightId)}/acknowledge`),
  /** Detecting the problem again reopens it. */
  resolve: (projectId: string, insightId: string): Promise<Insight> =>
    api.post<Insight>(`${insightPath(projectId, insightId)}/resolve`),
  /** Silence until `until` (at most 90 days ahead); `422 INVALID_MUTE` for a bad time or reason. */
  mute: (projectId: string, insightId: string, input: MuteInput): Promise<Insight> =>
    api.post<Insight>(`${insightPath(projectId, insightId)}/mute`, input),
  /** `409 INVALID_TRANSITION` unless the insight is muted. */
  unmute: (projectId: string, insightId: string): Promise<Insight> =>
    api.post<Insight>(`${insightPath(projectId, insightId)}/unmute`),
  /** Claude's advisory explanation (201). Spends the org's monthly explain budget. */
  explain: (projectId: string, insightId: string): Promise<Explanation> =>
    api.post<Explanation>(`${insightPath(projectId, insightId)}/explain`),
  /** Newest first, at most 100. */
  detectorRuns: (projectId: string, limit?: number): Promise<DetectorRun[]> =>
    api.get<DetectorRun[]>(`${projectPath(projectId)}/detector-runs`, { limit }),
  /** Records or replaces the caller's vote; needs `insights:feedback` (member and above). */
  putFeedback: (projectId: string, insightId: string, input: FeedbackInput): Promise<Feedback> =>
    api.put<Feedback>(`${insightPath(projectId, insightId)}/feedback`, input),
  /** Removes the caller's vote (204); `404` when there is none. */
  deleteFeedback: (projectId: string, insightId: string): Promise<void> =>
    api.delete(`${insightPath(projectId, insightId)}/feedback`),
  /** Precision per detector kind from member feedback. */
  precision: (projectId: string, query: PrecisionQuery = {}): Promise<PrecisionOut> =>
    api.get<PrecisionOut>(`${insightsPath(projectId)}/precision`, { ...query }),
  /** The health score over the window; `value` is `null` without LLM calls. */
  health: (projectId: string, query: HealthQuery = {}): Promise<Health> =>
    api.get<Health>(`${projectPath(projectId)}/health`, { ...query }),
};
