import { Check, CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";

import { Callout } from "@/components/callout";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import {
  CHIP_OF_STEP,
  DAILY_REVIEW_QUEUE,
  stepFailureText,
  stepFailureTitle,
  type ChipState,
  type SetupStep,
} from "./setup-steps";
import type { SetupRun } from "./use-setup-review";

const STEP_TITLES = ["Failure categories", "Queue", "Selection"] as const;

/** The dialog's three step chips; state is carried by an icon and the words, not colour alone. */
export function StepChips({ states }: { states: readonly ChipState[] }) {
  return (
    <ol className="flex flex-wrap gap-2" aria-label="Steps">
      {STEP_TITLES.map((title, index) => {
        const state = states[index] ?? "todo";
        return (
          <li key={title} aria-current={state === "current" ? "step" : undefined}>
            <Badge
              variant={
                state === "done"
                  ? "success"
                  : state === "failed"
                    ? "danger"
                    : state === "current"
                      ? "ink"
                      : "outline"
              }
            >
              {state === "done" ? <Check aria-hidden /> : null}
              {state === "failed" ? <CircleAlert aria-hidden /> : null}
              {index + 1} · {title}
              {state === "done" ? <span className="sr-only"> (done)</span> : null}
              {state === "failed" ? <span className="sr-only"> (failed)</span> : null}
            </Badge>
          </li>
        );
      })}
    </ol>
  );
}

function doneText(step: SetupStep, categories: number): { title: string; detail?: string } {
  if (step === "create_config") {
    return {
      title: "Failure categories created",
      detail: `failure_category with ${String(categories)} categories.`,
    };
  }
  return step === "create_queue"
    ? { title: `Queue “${DAILY_REVIEW_QUEUE}” created` }
    : { title: "Policy created" };
}

interface SetupProgressProps {
  run: SetupRun;
  /** The number of categories sent, for the first line. */
  categories: number;
}

/** "Setting up": each request with its outcome; a failed one says why. */
export function SetupProgress({ run, categories }: SetupProgressProps) {
  if (run.done) {
    return (
      <Callout tone="success" role="status" title="Daily review is set up">
        The first selection runs tomorrow at 01:00 UTC. Select yesterday now to start reviewing
        today.
      </Callout>
    );
  }
  return (
    <section className="flex flex-col gap-2" aria-label="Setting up">
      <h3 className="text-base font-bold text-foreground">Setting up</h3>
      <ol className="flex flex-col gap-2" aria-live="polite">
        {run.steps.map(({ step, status }) => {
          const number = CHIP_OF_STEP[step] + 1;
          const done = doneText(step, categories);
          return (
            <li
              key={step}
              className={cn(
                "flex items-start gap-3 rounded-tile px-4 py-3",
                status === "failed" ? "bg-danger-subtle text-danger-text" : "bg-surface-muted",
              )}
            >
              {status === "done" ? (
                <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-success" />
              ) : status === "failed" ? (
                <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              ) : (
                <LoaderCircle
                  aria-hidden
                  className="mt-0.5 size-4 shrink-0 motion-safe:animate-spin"
                />
              )}
              <span className="flex min-w-0 flex-col gap-0.5 text-sm">
                <span className="font-semibold">
                  {number} ·{" "}
                  {status === "failed"
                    ? stepFailureTitle(step)
                    : status === "done"
                      ? done.title
                      : "Working…"}
                </span>
                {status === "failed" ? (
                  <span role="alert" className="text-xs">
                    {stepFailureText(step, run.error)}
                  </span>
                ) : status === "done" && done.detail ? (
                  <span className="text-xs text-muted-foreground">{done.detail}</span>
                ) : null}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
