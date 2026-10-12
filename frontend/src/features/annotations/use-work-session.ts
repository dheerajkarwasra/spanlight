import { useCallback, useEffect, useReducer, useRef } from "react";

import { useRefreshScores } from "@/features/scores";
import { useProjectParams } from "@/features/shell";
import { annotationsApi, type CompletedScore } from "@/lib/api";

import { isClaimLost, workNotice } from "./annotation-errors";
import { useInvalidateQueues } from "./annotation-queries";
import { canClaim, INITIAL_WORK_STATE, workReducer, type SaveAction } from "./work-state";

/** The queue a mounted session works on; null once the page has gone. */
interface SessionKey {
  projectId: string;
  queueId: string;
}

function sameQueue(a: SessionKey | null, b: SessionKey): boolean {
  return a !== null && a.projectId === b.projectId && a.queueId === b.queueId;
}

function releaseQuietly(key: SessionKey, itemId: string): void {
  // Fire and forget: nobody is waiting. An unreleased claim still frees itself in 30 minutes.
  annotationsApi.release(key.projectId, key.queueId, itemId).catch(() => undefined);
}

/**
 * Drives one reviewer's work session on a queue: claims an item on mount, completes, skips or
 * releases it, then claims the next. Leaving the page releases a held item so it goes straight
 * back to the queue instead of waiting out the 30-minute claim timeout.
 *
 * Requests run one at a time (`busy`), and every step after an `await` first checks that the
 * page is still on the same queue (`active`), so an unmount never claims, never races a running
 * complete with a release, and never leaves a claim behind.
 */
export function useWorkSession(queueId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateQueues();
  const refreshScores = useRefreshScores();
  const [state, dispatch] = useReducer(workReducer, INITIAL_WORK_STATE);
  const active = useRef<SessionKey | null>(null);
  /** The item this session holds, and the trace it is about. */
  const held = useRef<string | null>(null);
  const heldTrace = useRef<string | null>(null);
  /** The request running now; a second one is refused until it settles. */
  const busy = useRef<SaveAction | "claim" | null>(null);
  /** Bumped when a claim is abandoned, so its late answer is not applied. */
  const claimSeq = useRef(0);
  const invalidateRef = useRef(invalidate);
  const refreshScoresRef = useRef(refreshScores);
  useEffect(() => {
    invalidateRef.current = invalidate;
    refreshScoresRef.current = refreshScores;
  });

  // Stable for the life of the component: it reads everything else through refs.
  const claimNext = useCallback(async () => {
    const key = active.current;
    if (key === null || busy.current !== null) {
      return;
    }
    busy.current = "claim";
    const seq = ++claimSeq.current;
    dispatch({ type: "claim_started" });
    let item;
    try {
      item = await annotationsApi.claim(key.projectId, key.queueId);
    } catch (error) {
      if (seq === claimSeq.current) {
        busy.current = null;
        dispatch({ type: "claim_failed", error });
      }
      return;
    }
    if (seq !== claimSeq.current) {
      // Abandoned. A remount on the same queue claims again and gets this same item back (a
      // claim returns the caller's own item first); otherwise give it back now.
      if (item !== undefined && !sameQueue(active.current, key)) {
        releaseQuietly(key, item.id);
      }
      return;
    }
    busy.current = null;
    held.current = item?.id ?? null;
    heldTrace.current = item?.trace_id ?? null;
    dispatch({ type: "claimed", item });
    void invalidateRef.current();
  }, []);

  useEffect(() => {
    const key: SessionKey = { projectId, queueId };
    active.current = key;
    void claimNext();
    return () => {
      active.current = null;
      claimSeq.current += 1;
      if (busy.current === "claim") {
        busy.current = null;
      }
      const itemId = held.current;
      held.current = null;
      // A running complete, skip or release settles the item itself; releasing it too would race.
      if (itemId !== null && busy.current === null) {
        releaseQuietly(key, itemId);
      }
    };
  }, [claimNext, projectId, queueId]);

  const save = useCallback(
    async (action: SaveAction, scores: CompletedScore[] = []) => {
      const key = active.current;
      const itemId = held.current;
      if (key === null || itemId === null || busy.current !== null) {
        return;
      }
      busy.current = action;
      dispatch({ type: "save_started", action });
      try {
        if (action === "complete") {
          await annotationsApi.complete(key.projectId, key.queueId, itemId, scores);
        } else if (action === "skip") {
          await annotationsApi.skip(key.projectId, key.queueId, itemId);
        } else {
          await annotationsApi.release(key.projectId, key.queueId, itemId);
        }
      } catch (error) {
        busy.current = null;
        const lost = isClaimLost(error);
        if (active.current !== key) {
          // The page closed during the request, and the cleanup left the item to it.
          if (!lost) {
            releaseQuietly(key, itemId);
          }
          return;
        }
        if (lost) {
          held.current = null;
        }
        if (lost && action === "release") {
          // The item is no longer ours either way: that is what releasing asked for.
          dispatch({ type: "saved", action });
          void invalidateRef.current();
          return;
        }
        dispatch({ type: "save_failed", notice: workNotice(error), lost });
        if (lost) {
          await claimNext();
        }
        return;
      }
      busy.current = null;
      held.current = null;
      const traceId = heldTrace.current;
      if (action === "complete" && traceId !== null) {
        // The scores now sit on the trace: its panel, the explorer column and the score-name
        // options refresh, even when the page has closed in the meantime.
        void refreshScoresRef.current(traceId);
      }
      if (active.current !== key) {
        return;
      }
      dispatch({ type: "saved", action });
      if (action === "release") {
        void invalidateRef.current();
        return;
      }
      await claimNext();
    },
    [claimNext],
  );

  return {
    state,
    complete: (scores: CompletedScore[]) => save("complete", scores),
    skip: () => save("skip"),
    release: () => save("release"),
    claim: () => (canClaim(state) ? claimNext() : Promise.resolve()),
  };
}
