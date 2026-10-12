import { Ban, CircleCheck, CircleX, Clock, LoaderCircle, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { RUN_STATE_LABELS, type RunState } from "./run-copy";

const STYLES: Record<RunState, { icon: LucideIcon; classes: string }> = {
  done: { icon: CircleCheck, classes: "bg-success-subtle text-success" },
  failed: { icon: CircleX, classes: "bg-danger-subtle text-danger-text" },
  running: { icon: LoaderCircle, classes: "bg-accent-subtle text-accent" },
  queued: { icon: Clock, classes: "border border-border-strong bg-surface text-muted-foreground" },
  // Skipped for budget is neutral: nothing broke, the cap did its job.
  skipped: { icon: Ban, classes: "text-muted-foreground" },
};

/** Figma "Evals/Run status": an icon and a word for each state, never colour alone. */
export function RunStatus({ state }: { state: RunState }) {
  const { icon: Icon, classes } = STYLES[state];
  return (
    <span
      className={cn(
        "inline-flex min-h-[26px] shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap",
        classes,
      )}
    >
      <Icon
        aria-hidden
        className={cn("size-3.5", state === "running" && "motion-safe:animate-spin")}
      />
      {RUN_STATE_LABELS[state]}
    </span>
  );
}
