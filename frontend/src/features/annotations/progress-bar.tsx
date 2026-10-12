import type { QueueProgress } from "@/lib/api";
import { cn } from "@/lib/utils";

import { progressRatio, progressTotal } from "./queue-format";

interface ProgressBarProps {
  progress: QueueProgress;
  /** `card` on white surfaces; `ink` inside the dark progress card (lime fill). */
  tone?: "card" | "ink";
  className?: string;
}

/**
 * Figma "Queues/Progress bar": a pill track filled by the finished share (done and skipped). A
 * finished queue turns the fill green. The bar is decorative: the words beside it always carry
 * the numbers, for screen readers too.
 */
export function ProgressBar({ progress, tone = "card", className }: ProgressBarProps) {
  const total = progressTotal(progress);
  const ratio = progressRatio(progress);
  const finished = total > 0 && progress.pending === 0;
  return (
    <div
      aria-hidden
      className={cn(
        "h-2.5 w-full overflow-hidden rounded-full border",
        tone === "ink"
          ? "border-transparent bg-hero-card-foreground"
          : "border-border bg-surface-muted",
        className,
      )}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-200 ease-out-quart motion-reduce:transition-none",
          tone === "ink" ? "bg-lime" : finished ? "bg-success" : "bg-accent",
        )}
        style={{ width: `${String(Math.round(ratio * 1000) / 10)}%` }}
      />
    </div>
  );
}
