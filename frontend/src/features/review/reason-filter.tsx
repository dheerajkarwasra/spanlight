import type { QueueProgress, ReviewReason } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { progressTotal } from "@/features/annotations/queue-format";

import { REVIEW_REASONS, reasonLabel } from "./reason-copy";
import { REASON_STYLES } from "./reason-style";

export type ReasonFilterValue = "all" | ReviewReason;

interface ReasonFilterProps {
  progress: QueueProgress;
  value: ReasonFilterValue;
  onChange: (value: ReasonFilterValue) => void;
}

const PILL =
  "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-semibold whitespace-nowrap transition-colors motion-reduce:transition-none [&_svg]:size-3.5";

/** Figma "Queue detail / Reason filter": "All" and the five reasons, each with its item count. */
export function ReasonFilter({ progress, value, onChange }: ReasonFilterProps) {
  const count = (n: number) => formatInteger(n) ?? String(n);
  const options: { value: ReasonFilterValue; total: number }[] = [
    { value: "all", total: progressTotal(progress) },
    ...REVIEW_REASONS.map((reason) => ({
      value: reason,
      total: progressTotal(progress.by_reason[reason]),
    })),
  ];
  return (
    <div role="group" aria-label="Reason" className="flex flex-wrap items-center gap-1.5">
      <span aria-hidden className="mr-1 text-xs font-medium text-muted-foreground">
        Reason
      </span>
      {options.map((option) => {
        const Icon = option.value === "all" ? null : REASON_STYLES[option.value].icon;
        const pressed = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={pressed}
            onClick={() => onChange(option.value)}
            className={cn(
              PILL,
              pressed
                ? "border-transparent bg-hero-card text-hero-card-foreground"
                : "border-border-strong bg-surface text-foreground hover:bg-surface-hover",
            )}
          >
            {Icon === null ? null : <Icon aria-hidden />}
            {option.value === "all" ? "All" : reasonLabel(option.value)}
            <span className={cn("tabular", !pressed && "text-muted-foreground")}>
              {count(option.total)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
