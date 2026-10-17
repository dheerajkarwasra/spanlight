import { useRef, useState } from "react";

import { useProjectParams } from "@/features/shell";
import { annotationsApi, reviewApi, scoresApi } from "@/lib/api";

import { useInvalidateReview } from "./review-queries";
import {
  DAILY_REVIEW_QUEUE,
  selectionInput,
  setupPlan,
  type SelectionDraft,
  type SetupStep,
} from "./setup-steps";

const QUEUE_INSTRUCTIONS =
  "Read the trace, then label its failure_category. Use none when the answer was fine.";

/** What the confirm sends: the step 1 list, the step 2 pick (null to create) and step 3. */
export interface SetupDraft {
  categories: readonly string[];
  queueId: string | null;
  selection: SelectionDraft;
}

export type StepStatus = "running" | "done" | "failed";

export interface SetupRun {
  /** Every step attempted so far, in order, with how it went. */
  steps: { step: SetupStep; status: StepStatus }[];
  /** The error of the failed step; null while running or once done. */
  error: unknown;
  done: boolean;
}

/**
 * Runs the set-up plan in order: the failure-category config (if missing), the "Daily review"
 * queue (if none was picked), then the policy. A failed step stops the run; a retry skips the
 * steps that already succeeded, so nothing is created twice.
 */
export function useSetupReview(existingConfigId: string | null) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateReview();
  const made = useRef({ configId: existingConfigId, queueId: null as string | null });
  const [run, setRun] = useState<SetupRun | null>(null);

  async function runStep(step: SetupStep, draft: SetupDraft): Promise<void> {
    const ids = made.current;
    if (step === "create_config") {
      ids.configId = (await scoresApi.createFailureCategory(projectId, [...draft.categories])).id;
    } else if (step === "create_queue") {
      if (ids.configId === null) {
        throw new Error("The failure_category config must exist before its queue.");
      }
      const queue = await annotationsApi.create(projectId, {
        name: DAILY_REVIEW_QUEUE,
        instructions: QUEUE_INSTRUCTIONS,
        score_config_ids: [ids.configId],
      });
      ids.queueId = queue.id;
    } else {
      const queueId = draft.queueId ?? ids.queueId;
      if (queueId === null) {
        throw new Error("The policy needs a queue.");
      }
      await reviewApi.put(projectId, selectionInput(draft.selection, queueId));
    }
  }

  async function start(draft: SetupDraft): Promise<void> {
    const ids = made.current;
    const plan = setupPlan({ configId: ids.configId, queueId: draft.queueId ?? ids.queueId });
    const finished = (run?.steps ?? []).filter(
      (entry) => entry.status === "done" && !plan.includes(entry.step),
    );
    const steps = [...finished];
    try {
      for (const step of plan) {
        steps.push({ step, status: "running" });
        setRun({ steps: [...steps], error: null, done: false });
        await runStep(step, draft);
        steps[steps.length - 1] = { step, status: "done" };
      }
      setRun({ steps, error: null, done: true });
    } catch (error) {
      const failed = steps.at(-1);
      if (failed) {
        steps[steps.length - 1] = { step: failed.step, status: "failed" };
      }
      setRun({ steps, error, done: false });
    } finally {
      void invalidate();
    }
  }

  return { run, start, running: run?.steps.some((entry) => entry.status === "running") ?? false };
}
