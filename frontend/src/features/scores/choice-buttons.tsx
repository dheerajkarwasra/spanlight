import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface Choice<T extends string> {
  value: T;
  label: ReactNode;
  /** The accessible name when the label is a symbol, e.g. "At least" for "≥". */
  ariaLabel?: string;
}

/** How the group is named and described for assistive technology. */
interface ChoiceA11y {
  /** The id of the visible label of the group. */
  labelledBy: string;
  /** The id of an error or hint about the group. */
  describedBy?: string | undefined;
}

interface ChoiceState {
  disabled?: boolean;
  /** Outlines the unpicked options in red while the field has an error. */
  invalid?: boolean;
}

interface ChoiceButtonsProps<T extends string> {
  choices: readonly Choice<T>[];
  value: T | null;
  onChange: (value: T) => void;
  a11y: ChoiceA11y;
  state?: ChoiceState;
}

/**
 * Figma "Add score" Yes / No and category options, the score config type picker and the
 * comparator of the score filter: equal-width pills, the picked one outlined in violet with a
 * check. Each is a toggle button, so Tab reaches every option and Space or Enter picks it.
 */
export function ChoiceButtons<T extends string>({
  choices,
  value,
  onChange,
  a11y,
  state = {},
}: ChoiceButtonsProps<T>) {
  const { disabled = false, invalid = false } = state;
  return (
    <div
      role="group"
      aria-labelledby={a11y.labelledBy}
      aria-describedby={a11y.describedBy}
      className="flex flex-wrap gap-2"
    >
      {choices.map((choice) => {
        const picked = choice.value === value;
        return (
          <button
            key={choice.value}
            type="button"
            aria-pressed={picked}
            aria-label={choice.ariaLabel}
            disabled={disabled}
            onClick={() => {
              onChange(choice.value);
            }}
            className={cn(
              "inline-flex h-10 min-w-0 flex-1 basis-20 items-center justify-center gap-1.5 rounded-input px-3 text-sm text-foreground transition-colors",
              "disabled:pointer-events-none disabled:opacity-50",
              picked
                ? "border-2 border-accent bg-surface-selected font-semibold"
                : "border border-border-strong bg-surface font-medium hover:bg-surface-hover",
              invalid && !picked && "border-danger",
            )}
          >
            <span className="truncate">{choice.label}</span>
            {picked ? <Check aria-hidden className="size-3.5 shrink-0" strokeWidth={2.5} /> : null}
          </button>
        );
      })}
    </div>
  );
}
