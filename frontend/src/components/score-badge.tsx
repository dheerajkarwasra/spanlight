import { Check, X } from "lucide-react";

import type { ScoreDataType } from "@/lib/api";
import { formatScoreValue, type ScoreValueParts } from "@/lib/score-format";
import { cn } from "@/lib/utils";

interface ScoreBadgeProps {
  score: ScoreValueParts;
  /** The score's type; null when its name has no config (a value then reads as a number). */
  dataType: ScoreDataType | null;
  /** `md` on score tiles, `sm` in dense rows and tables. */
  size?: "md" | "sm";
  className?: string;
}

/**
 * Figma "Scores/Value": one score value the same way on every screen. A number in mono with at
 * most four decimals, Yes or No with a check or a cross, a category in an outlined chip.
 */
export function ScoreBadge({ score, dataType, size = "md", className }: ScoreBadgeProps) {
  const text = formatScoreValue(score, dataType);
  const small = size === "sm";
  if (dataType === "boolean" && score.string_value === null && score.value !== null) {
    const yes = score.value === 1;
    const Icon = yes ? Check : X;
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 font-bold text-foreground",
          small ? "text-xs" : "text-sm",
          className,
        )}
      >
        <Icon
          aria-hidden
          className={cn(small ? "size-3.5" : "size-4", yes ? "text-success" : "text-danger-text")}
          strokeWidth={2.5}
        />
        {text}
      </span>
    );
  }
  if (score.string_value !== null) {
    return (
      <span
        title={text}
        className={cn(
          "inline-flex max-w-48 min-w-0 items-center rounded-full border border-border-strong bg-surface px-2 py-0.5 font-medium text-foreground",
          small ? "text-2xs" : "text-xs",
          className,
        )}
      >
        <span className="truncate">{text}</span>
      </span>
    );
  }
  return (
    <span
      className={cn(
        "font-mono font-bold text-foreground tabular",
        small ? "text-sm" : "text-base",
        className,
      )}
    >
      {text}
    </span>
  );
}
