import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";
import { toast } from "sonner";

import { FAILURE_CATEGORY_SCORE } from "@/features/scores";
import { useProjectParams } from "@/features/shell";
import { datasetsApi, queryKeys, type CompletedScore } from "@/lib/api";

import type { ReviewMark } from "./save-from-review";

/** A dataset item saved from a queue item, with the metadata its PATCH wrote. */
export interface ReviewCopy {
  datasetId: string;
  itemId: string;
  metadata: Record<string, unknown>;
}

/**
 * G-004: "Save as dataset item" runs before Submit, so the copy carries the draft failure
 * category. When the queue item ends, each copy saved for it gets the submitted label, or none on
 * a skip or release. shortcut: a copy saved and then left (page closed without submitting) keeps
 * the draft; upgrade if the backend ever writes the label from the score itself.
 */
export function useReviewCopies() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  const copies = useRef(new Map<string, ReviewCopy[]>());

  const record = useCallback((queueItemId: string, copy: ReviewCopy) => {
    const saved = copies.current.get(queueItemId) ?? [];
    copies.current.set(queueItemId, [
      ...saved.filter((other) => other.itemId !== copy.itemId),
      copy,
    ]);
  }, []);

  const settle = useCallback(
    async (
      queueItemId: string,
      action: "complete" | "skip" | "release",
      scores: CompletedScore[],
    ) => {
      const saved = copies.current.get(queueItemId);
      if (saved === undefined) return;
      copies.current.delete(queueItemId);
      const label =
        action === "complete"
          ? (scores.find((score) => score.name === FAILURE_CATEGORY_SCORE)?.string_value ?? null)
          : null;
      const results = await Promise.allSettled(
        saved.map(async (copy) => {
          const review = copy.metadata.review as ReviewMark;
          if (review.failure_category === label) return;
          await datasetsApi.updateItem(projectId, copy.datasetId, copy.itemId, {
            metadata: { ...copy.metadata, review: { ...review, failure_category: label } },
          });
        }),
      );
      if (results.some((result) => result.status === "rejected")) {
        toast.error("The dataset item still has the draft label. Set it in the dataset.");
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).datasets.all });
    },
    [projectId, queryClient],
  );

  return { record, settle };
}
