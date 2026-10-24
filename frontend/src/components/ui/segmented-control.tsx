import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
  /**
   * Extra words for screen readers, appended to the visible label so the accessible name still
   * starts with what sighted and voice-control users see, e.g. "last 24 hours" for "24h".
   */
  srLabel?: string;
  disabled?: boolean;
}

interface SegmentedControlProps<T extends string> {
  /** The selected option, or null when none applies (e.g. a custom range is active). */
  value: T | null;
  onValueChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  /** Names the group for assistive technology, e.g. "Time range". */
  "aria-label": string;
  /** Points at text that explains the group, e.g. why some options are disabled. */
  "aria-describedby"?: string;
  /**
   * `ink`: white pill track, the active segment filled with ink (Figma "Control/Time range").
   * `surface`: muted track, the active segment raised on white (Figma "Snippet tab").
   */
  tone?: "ink" | "surface";
  className?: string;
}

/**
 * A single-choice pill control with radio semantics: Tab focuses the group, arrow keys move
 * between options. Use it to pick a value; use `Tabs` to switch between panels.
 */
function SegmentedControl<T extends string>({
  value,
  onValueChange,
  options,
  "aria-label": ariaLabel,
  "aria-describedby": ariaDescribedBy,
  tone = "ink",
  className,
}: SegmentedControlProps<T>) {
  return (
    <RadioGroupPrimitive.Root
      value={value ?? ""}
      onValueChange={(next) => {
        const option = options.find((candidate) => candidate.value === next);
        if (option) {
          onValueChange(option.value);
        }
      }}
      aria-label={ariaLabel}
      aria-describedby={ariaDescribedBy}
      orientation="horizontal"
      className={cn(
        "inline-flex shrink-0 items-center gap-0.5 rounded-full border border-border p-1",
        tone === "ink" ? "bg-surface shadow-card" : "bg-surface-muted",
        className,
      )}
    >
      {options.map((option) => (
        <RadioGroupPrimitive.Item
          key={option.value}
          value={option.value}
          disabled={option.disabled}
          className={cn(
            "inline-flex items-center justify-center rounded-full px-3.5 py-1.5 text-sm font-medium whitespace-nowrap text-muted-foreground",
            "transition-[background-color,color,box-shadow] duration-200 ease-out-quart",
            "data-[state=unchecked]:hover:text-foreground",
            "focus-visible:outline-offset-0 disabled:pointer-events-none disabled:opacity-50",
            "data-[state=checked]:font-semibold",
            tone === "ink"
              ? "data-[state=checked]:bg-ink data-[state=checked]:text-ink-foreground"
              : // Dark: the fill is ~1.07:1 on the track, so a ring (3.4:1) marks it. shadow-none, since
                // the dark card shadow is `none`, which would void the composed ring box-shadow.
                "data-[state=checked]:bg-surface data-[state=checked]:text-foreground data-[state=checked]:shadow-card dark:data-[state=checked]:bg-surface-hover dark:data-[state=checked]:shadow-none dark:data-[state=checked]:ring-1 dark:data-[state=checked]:ring-border-strong dark:data-[state=checked]:ring-inset",
          )}
        >
          {option.label}
          {option.srLabel ? <span className="sr-only">, {option.srLabel}</span> : null}
        </RadioGroupPrimitive.Item>
      ))}
    </RadioGroupPrimitive.Root>
  );
}

export { SegmentedControl };
export type { SegmentedControlProps, SegmentedOption };
