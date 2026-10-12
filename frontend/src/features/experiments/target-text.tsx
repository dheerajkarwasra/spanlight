import { FileText } from "lucide-react";

import type { ExperimentTarget } from "@/lib/api";
import { cn } from "@/lib/utils";

import { targetLabel, type VersionIndex } from "./run-label";

interface TargetTextProps {
  target: ExperimentTarget;
  index: VersionIndex;
  className?: string;
}

/** What an experiment runs: the prompt version (violet) or "Ad-hoc messages", over the model. */
export function TargetText({ target, index, className }: TargetTextProps) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", className)}>
      <span
        className={cn(
          "truncate text-sm font-medium",
          target.kind === "prompt_version" ? "text-accent" : "text-foreground",
        )}
      >
        <span className="sr-only">Target </span>
        {targetLabel(target, index)}
      </span>
      <span className="truncate font-mono text-[11px] text-muted-foreground">{target.model}</span>
    </div>
  );
}

/** The target as the detail header's chips: the prompt version pill and the model pill. */
export function TargetChips({ target, index }: { target: ExperimentTarget; index: VersionIndex }) {
  return (
    <>
      {target.kind === "prompt_version" ? (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-subtle px-2.5 py-0.5 text-xs font-semibold text-accent">
          <FileText aria-hidden className="size-3.5" />
          {targetLabel(target, index)}
        </span>
      ) : (
        <span className="rounded-full bg-surface-muted px-2.5 py-0.5 text-xs font-semibold text-foreground">
          Ad-hoc messages
        </span>
      )}
      <span className="rounded-full border border-border-strong px-2.5 py-0.5 font-mono text-[11px] text-foreground">
        {target.model}
      </span>
    </>
  );
}
