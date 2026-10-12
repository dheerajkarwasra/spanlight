import type { CompletedScore, QueueItem, ScoreConfig } from "@/lib/api";

import { toCompletedScores, type ScoreDraft } from "./score-draft";

/**
 * The work-mode state machine: claim → fill → complete (or skip) → claim the next item. Releasing
 * leaves the reviewer with no item until they claim again; a claim that finds nothing ends the
 * session ("All done"). Pure: the hook in `use-work-session.ts` performs the requests.
 */

export type SaveAction = "complete" | "skip" | "release";

/** A message that outlives the item it was about, e.g. "This item was reassigned". */
export interface WorkNotice {
  title: string;
  message: string;
}

export type WorkState =
  | { phase: "claiming"; item: QueueItem | null; notice: WorkNotice | null }
  | { phase: "working"; item: QueueItem; saving: SaveAction | null; notice: WorkNotice | null }
  | { phase: "released" }
  | { phase: "empty" }
  | { phase: "failed"; error: unknown };

export type WorkEvent =
  | { type: "claim_started" }
  | { type: "claimed"; item: QueueItem | undefined }
  | { type: "claim_failed"; error: unknown }
  | { type: "save_started"; action: SaveAction }
  | { type: "saved"; action: SaveAction }
  /** The save failed; `lost` when the item is no longer this reviewer's to finish. */
  | { type: "save_failed"; notice: WorkNotice; lost: boolean };

export const INITIAL_WORK_STATE: WorkState = { phase: "claiming", item: null, notice: null };

function noticeOf(state: WorkState): WorkNotice | null {
  return state.phase === "claiming" || state.phase === "working" ? state.notice : null;
}

export function workReducer(state: WorkState, event: WorkEvent): WorkState {
  switch (event.type) {
    case "claim_started":
      return {
        phase: "claiming",
        item: state.phase === "working" ? state.item : null,
        notice: noticeOf(state),
      };
    case "claimed":
      return event.item === undefined
        ? { phase: "empty" }
        : { phase: "working", item: event.item, saving: null, notice: noticeOf(state) };
    case "claim_failed":
      return { phase: "failed", error: event.error };
    case "save_started":
      return state.phase === "working" && state.saving === null
        ? { ...state, saving: event.action, notice: null }
        : state;
    case "saved":
      if (state.phase !== "working") {
        return state;
      }
      return event.action === "release"
        ? { phase: "released" }
        : { phase: "claiming", item: state.item, notice: null };
    case "save_failed":
      if (state.phase !== "working") {
        return state;
      }
      return event.lost
        ? { phase: "claiming", item: state.item, notice: event.notice }
        : { ...state, saving: null, notice: event.notice };
  }
}

/** The item on screen, if any: kept while the next one loads so the layout does not jump. */
export function visibleItem(state: WorkState): QueueItem | null {
  return state.phase === "working" || state.phase === "claiming" ? state.item : null;
}

/** The claimed item a save may act on, or null while busy or holding nothing. */
export function actionableItem(state: WorkState): QueueItem | null {
  return state.phase === "working" && state.saving === null ? state.item : null;
}

/**
 * The scores to complete the item with, or null when completing is refused: no item held, a save
 * already running, or a required score still empty or invalid.
 */
export function completionFor(
  state: WorkState,
  configs: readonly ScoreConfig[],
  draft: ScoreDraft,
  comment: string,
): CompletedScore[] | null {
  return actionableItem(state) === null ? null : toCompletedScores(configs, draft, comment);
}

/** `N` and "Claim next item" act only when no item is held. */
export function canClaim(state: WorkState): boolean {
  return state.phase === "released" || state.phase === "failed";
}
