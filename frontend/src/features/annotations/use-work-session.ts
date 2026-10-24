import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { useRefreshScores } from "@/features/scores";
import { useProjectParams } from "@/features/shell";
import { annotationsApi, type CompletedScore } from "@/lib/api";

import { submittedValues, type ScoreValue } from "./agreement";
import { isClaimLost, workNotice } from "./annotation-errors";
import { useInvalidateQueues } from "./annotation-queries";
import { claimFor, type InFlightClaim, type SessionKey } from "./in-flight-claims";
import { canClaim, INITIAL_WORK_STATE, workReducer, type SaveAction } from "./work-state";

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
  /** What the reviewer sent for the item they completed last, for the "Last item" strip. */
  const [lastCompleted, setLastCompleted] = useState<{
    traceId: string;
    values: Record<string, ScoreValue>;
  } | null>(null);
  const active = useRef<SessionKey | null>(null);
  /** The item this session holds, and the trace it is about. */
  const held = useRef<string | null>(null);
  const heldTrace = useRef<string | null>(null);
  /** The request running now; a second one is refused until it settles. */
  const busy = useRef<SaveAction | "claim" | null>(null);
  /** Bumped when a claim is abandoned, so its late answer is not applied. */
  const claimSeq = useRef(0);
  /** The claim this session is waiting on, so leaving the page can hand it on. */
  const claiming = useRef<InFlightClaim | null>(null);
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
    const claim = claimFor(key);
    claiming.current = claim;
    let item;
    try {
      item = await claim.answer;
    } catch (error) {
      if (seq === claimSeq.current) {
        busy.current = null;
        claiming.current = null;
        dispatch({ type: "claim_failed", error });
      }
      return;
    }
    if (seq !== claimSeq.current) {
      // Abandoned. A session that adopted this claim shows the item; otherwise give it back now.
      if (item !== undefined && claim.orphaned && !claim.released) {
        claim.released = true;
        releaseQuietly(key, item.id);
      }
      return;
    }
    busy.current = null;
    claiming.current = null;
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
      setLastCompleted(null);
      claimSeq.current += 1;
      if (busy.current === "claim") {
        busy.current = null;
        if (claiming.current !== null) {
          claiming.current.orphaned = true;
          claiming.current = null;
        }
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
      // A skip or release ends the strip too: it only ever names the item finished last.
      setLastCompleted(
        action === "complete" && traceId !== null
          ? { traceId, values: submittedValues(scores) }
          : null,
      );
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
    lastCompleted,
    complete: (scores: CompletedScore[]) => save("complete", scores),
    skip: () => save("skip"),
    release: () => save("release"),
    claim: () => (canClaim(state) ? claimNext() : Promise.resolve()),
  };
}
