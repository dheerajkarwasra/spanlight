import {
  CircleCheck,
  CircleX,
  Clock,
  LoaderCircle,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { ExperimentRun } from "@/lib/api";
import { cn } from "@/lib/utils";

import { runState, type RunState } from "./run-label";

const STATES: Record<
  RunState,
  { label: string; icon: LucideIcon; variant: BadgeProps["variant"]; spin?: boolean }
> = {
  queued: { label: "Queued", icon: Clock, variant: "neutral" },
  running: { label: "Running", icon: LoaderCircle, variant: "accent", spin: true },
  done: { label: "Done", icon: CircleCheck, variant: "success" },
  stopped: { label: "Stopped", icon: TriangleAlert, variant: "warning" },
  failed: { label: "Failed", icon: CircleX, variant: "danger" },
  ended: { label: "Ended early", icon: TriangleAlert, variant: "warning" },
};

/** Figma "Evals/Run status": the state in words with its icon, never colour alone. */
export function RunStatusPill({
  run,
  className,
}: {
  /** `error` undefined (not known here) shows a failed run as "Ended early". */
  run: { status: string; error?: ExperimentRun["error"] };
  className?: string;
}) {
  const state = STATES[runState(run)];
  const Icon = state.icon;
  return (
    <Badge variant={state.variant} className={className}>
      <Icon aria-hidden className={cn(state.spin && "motion-safe:animate-spin")} />
      {state.label}
    </Badge>
  );
}
