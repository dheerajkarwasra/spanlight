import { api } from "./client";
import { projectPath } from "./paths";
import type { ScoreNumber } from "./scores";
import type { Page } from "./types";

export type QueueItemStatus = "pending" | "done" | "skipped";

/** Why an item is in its queue: added by hand, or picked by the daily review policy. */
export type ReviewReason = "manual" | "doctor_finding" | "error" | "cost_outlier" | "random";

export interface ReasonProgress {
  pending: number;
  done: number;
  skipped: number;
}

export interface QueueProgress extends ReasonProgress {
  /** The same counts per reason; every reason is present, zeros included. */
  by_reason: Record<ReviewReason, ReasonProgress>;
}

/**
 * What the selection recorded about an item: numbers, ids and the trace name, never payload
 * text. Which keys are present depends on the item's reason; money and rates are 4-place decimal
 * strings. A key the server did not send is unknown.
 */
export interface ReasonDetail {
  /** `doctor_finding`: the open insight and its detector kind. */
  insight_id?: string;
  kind?: string;
  /** `error`: failed spans in the trace, the first of them, and its error class. */
  error_count?: number;
  first_error_span_id?: string | null;
  error_class?: string | null;
  /** `cost_outlier`: the trace's cost against the p99 of its name over 7 days (`sample` traces). */
  cost_usd?: string;
  p99_usd?: string;
  trace_name?: string;
  sample?: number;
  /** `random`: the policy's random share when the item was picked. */
  rate?: string;
  /** The lower-priority reasons the trace matched too. */
  also?: ReviewReason[];
}

export interface AnnotationQueue {
  id: string;
  name: string;
  instructions: string | null;
  /** The score configs a reviewer fills in for each item. */
  score_config_ids: string[];
  progress: QueueProgress;
  created_at: string;
}

export interface QueueCreate {
  name: string;
  instructions?: string | null;
  score_config_ids: string[];
}

export interface QueueItem {
  id: string;
  trace_id: string;
  span_id: string | null;
  status: QueueItemStatus;
  /**
   * Why the item was queued and what the selection saw; the server always sends both. A missing
   * `reason` reads as `manual`.
   * shortcut: optional only because `use-work-session.test.ts`'s fixture predates reasons; make
   * both required once that fixture sets them.
   */
  reason?: ReviewReason;
  reason_detail?: ReasonDetail | null;
  /** The reviewer holding the item; null while it waits. */
  assigned_to: string | null;
  assigned_at: string | null;
  completed_by: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface QueueItemListQuery {
  status?: QueueItemStatus | undefined;
  reason?: ReviewReason | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

export interface QueueItemsAdded {
  added: number;
  skipped_duplicates: number;
}

/** One score of a completed review: exactly one of `value` and `string_value`. */
export interface CompletedScore {
  name: string;
  value?: boolean | ScoreNumber | null;
  string_value?: string | null;
  comment?: string | null;
}

function queuesPath(projectId: string): string {
  return `${projectPath(projectId)}/annotation-queues`;
}

function queuePath(projectId: string, queueId: string): string {
  return `${queuesPath(projectId)}/${encodeURIComponent(queueId)}`;
}

function itemPath(projectId: string, queueId: string, itemId: string): string {
  return `${queuePath(projectId, queueId)}/items/${encodeURIComponent(itemId)}`;
}

/**
 * Annotation (review) queues under `/projects/{id}/annotation-queues`. Reads need `project:read`;
 * everything that changes a queue or an item needs `annotations:write` (member). Errors:
 * `409 NAME_TAKEN`, `409 QUEUE_IN_USE` (deleting the queue a review policy fills),
 * `409 ITEM_NOT_ASSIGNED_TO_YOU`, `409 ITEM_NOT_PENDING`, `422 UNKNOWN_SCORE_CONFIG`,
 * `422 SCORE_NOT_IN_QUEUE`, `422 SCORE_REQUIRED`.
 */
export const annotationsApi = {
  list: (
    projectId: string,
    query: { limit?: number; cursor?: string | null } = {},
  ): Promise<Page<AnnotationQueue>> =>
    api.get<Page<AnnotationQueue>>(queuesPath(projectId), { ...query }),
  get: (projectId: string, queueId: string): Promise<AnnotationQueue> =>
    api.get<AnnotationQueue>(queuePath(projectId, queueId)),
  create: (projectId: string, input: QueueCreate): Promise<AnnotationQueue> =>
    api.post<AnnotationQueue>(queuesPath(projectId), input),
  delete: (projectId: string, queueId: string): Promise<void> =>
    api.delete(queuePath(projectId, queueId)),

  items: (
    projectId: string,
    queueId: string,
    query: QueueItemListQuery = {},
  ): Promise<Page<QueueItem>> =>
    api.get<Page<QueueItem>>(`${queuePath(projectId, queueId)}/items`, { ...query }),
  /** Queues the traces (or one span of each); traces already queued are `skipped_duplicates`. */
  addItems: (
    projectId: string,
    queueId: string,
    traceIds: string[],
    spanId?: string | null,
  ): Promise<QueueItemsAdded> =>
    api.post<QueueItemsAdded>(`${queuePath(projectId, queueId)}/items`, {
      trace_ids: traceIds,
      ...(spanId ? { span_id: spanId } : {}),
    }),
  /** The next item for this reviewer, or `undefined` when nothing is waiting (204). */
  claim: (projectId: string, queueId: string): Promise<QueueItem | undefined> =>
    api.post<QueueItem | undefined>(`${queuePath(projectId, queueId)}/claim`),
  /** Saves the scores as human scores and marks the item done. */
  complete: (
    projectId: string,
    queueId: string,
    itemId: string,
    scores: CompletedScore[],
  ): Promise<QueueItem> =>
    api.post<QueueItem>(`${itemPath(projectId, queueId, itemId)}/complete`, { scores }),
  skip: (projectId: string, queueId: string, itemId: string): Promise<QueueItem> =>
    api.post<QueueItem>(`${itemPath(projectId, queueId, itemId)}/skip`),
  /** Gives the item back to the queue. */
  release: (projectId: string, queueId: string, itemId: string): Promise<QueueItem> =>
    api.post<QueueItem>(`${itemPath(projectId, queueId, itemId)}/release`),
};
