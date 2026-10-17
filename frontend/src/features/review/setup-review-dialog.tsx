import { useEffect, useRef, useState } from "react";

import { DialogHeading } from "@/components/dialog-heading";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import type { AnnotationQueue, ScoreConfig } from "@/lib/api";

import { SetupCategoriesStep } from "./setup-categories-step";
import { SetupFooter } from "./setup-footer";
import { SetupProgress, StepChips } from "./setup-progress";
import { SetupQueueStep } from "./setup-queue-step";
import { SetupSelectionStep } from "./setup-selection-step";
import {
  DAILY_REVIEW_QUEUE,
  DEFAULT_FAILURE_CATEGORIES,
  DEFAULT_SELECTION,
  FAILURE_CATEGORY_SCORE,
  capError,
  eligibleQueues,
  runChipStates,
  selectsNothing,
  type ChipState,
} from "./setup-steps";
import { useSetupReview } from "./use-setup-review";

const TITLE = "Set up daily review";
const DESCRIPTION =
  "Every day Spanlight queues the traces worth reading. Nothing is created until you confirm.";

interface SetupReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  queues: readonly AnnotationQueue[] | undefined;
  configs: readonly ScoreConfig[] | undefined;
}

/**
 * Figma "Set up daily review": failure categories, queue, selection; the confirm creates what is
 * missing in that order (spec §5.2), reports the step that failed, and offers to select yesterday.
 */
export function SetupReviewDialog({ open, onOpenChange, queues, configs }: SetupReviewDialogProps) {
  const [locked, setLocked] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!locked) {
          onOpenChange(next);
        }
      }}
    >
      {open ? (
        <DialogContent hideClose className="max-w-[640px] gap-5 rounded-card p-5 sm:p-7">
          {queues === undefined || configs === undefined ? (
            <>
              <DialogHeading title={TITLE} description={DESCRIPTION} />
              <Skeleton role="status" aria-label="Loading queues" className="h-48 rounded-tile" />
            </>
          ) : (
            <SetupFlow
              queues={queues}
              configs={configs}
              onLock={setLocked}
              onClose={() => onOpenChange(false)}
            />
          )}
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface SetupFlowProps {
  queues: readonly AnnotationQueue[];
  configs: readonly ScoreConfig[];
  onLock: (locked: boolean) => void;
  onClose: () => void;
}

function SetupFlow({ queues, configs, onLock, onClose }: SetupFlowProps) {
  // Read once: a config created by this flow must not move the dialog back a step.
  const [configId] = useState(
    () => configs.find((config) => config.name === FAILURE_CATEGORY_SCORE)?.id ?? null,
  );
  const first = configId === null ? 0 : 1;
  const [step, setStep] = useState(first);
  const [categories, setCategories] = useState<string[]>([...DEFAULT_FAILURE_CATEGORIES]);
  const [queueId, setQueueId] = useState(() => eligibleQueues(queues, configId)[0]?.id ?? null);
  const [selection, setSelection] = useState(DEFAULT_SELECTION);
  const [showRun, setShowRun] = useState(false);
  const setup = useSetupReview(configId);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const run = showRun ? setup.run : null;

  useEffect(() => onLock(setup.running), [onLock, setup.running]);
  useEffect(() => titleRef.current?.focus(), [step, showRun]);

  const valid = [
    categories.length >= 2,
    true,
    capError(selection.dailyCap) === null && !selectsNothing(selection),
  ][step];
  const chips: ChipState[] =
    run === null
      ? [0, 1, 2].map((index) =>
          index < step || index < first ? "done" : index === step ? "current" : "todo",
        )
      : runChipStates(run);
  const queueName =
    queues.find((queue) => queue.id === queueId)?.name ?? `the new “${DAILY_REVIEW_QUEUE}” queue`;

  return (
    <form
      noValidate
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (run !== null || !valid) {
          return;
        }
        if (step < 2) {
          setStep(step + 1);
        } else {
          setShowRun(true);
          void setup.start({ categories, queueId, selection });
        }
      }}
    >
      <DialogHeading
        title={TITLE}
        description={DESCRIPTION}
        showClose={setup.running ? "disabled" : true}
        titleRef={titleRef}
      />
      <StepChips states={chips} />
      {run !== null ? <SetupProgress run={run} categories={categories.length} /> : null}
      {run === null && step === 0 ? (
        <SetupCategoriesStep categories={categories} onChange={setCategories} />
      ) : null}
      {run === null && step === 1 ? (
        <SetupQueueStep
          queues={queues}
          configs={configs}
          configId={configId}
          value={queueId}
          onChange={setQueueId}
        />
      ) : null}
      {run === null && step === 2 ? (
        <SetupSelectionStep draft={selection} onChange={setSelection} queueName={queueName} />
      ) : null}
      <SetupFooter
        position={{ step, first, valid: valid ?? false }}
        run={run}
        onBack={() => (run === null ? setStep(step - 1) : setShowRun(false))}
        onRetry={() => void setup.start({ categories, queueId, selection })}
        onClose={onClose}
      />
    </form>
  );
}
