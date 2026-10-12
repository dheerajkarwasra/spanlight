import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import { useId } from "react";

import { cn } from "@/lib/utils";

export interface SpanChoiceOption {
  value: string;
  title: string;
  detail: string;
  disabled?: boolean;
}

interface SpanChoiceProps {
  label: string;
  value: string;
  options: SpanChoiceOption[];
  onChange: (value: string) => void;
}

/**
 * Figma "Which span" / "What to review": radio cards, the chosen one violet-tinted. A radio group,
 * so arrow keys move between the choices.
 */
export function SpanChoice({ label, value, options, onChange }: SpanChoiceProps) {
  const labelId = useId();
  return (
    <div className="flex flex-col gap-2">
      <span id={labelId} className="text-label font-semibold text-foreground">
        {label}
      </span>
      <RadioGroupPrimitive.Root
        value={value}
        onValueChange={onChange}
        aria-labelledby={labelId}
        className="flex flex-col gap-2"
      >
        {options.map((option) => (
          <RadioGroupPrimitive.Item
            key={option.value}
            value={option.value}
            disabled={option.disabled}
            className={cn(
              "group flex items-center gap-3 rounded-input px-3.5 py-3 text-left transition-colors",
              "border border-border-strong bg-surface hover:bg-surface-muted",
              "data-[state=checked]:border-[1.5px] data-[state=checked]:border-accent data-[state=checked]:bg-surface-selected",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          >
            <span className="flex size-[18px] shrink-0 items-center justify-center rounded-full border-[1.5px] border-subtle-foreground group-data-[state=checked]:border-accent">
              <RadioGroupPrimitive.Indicator className="size-2.5 rounded-full bg-accent" />
            </span>
            <span className="flex min-w-0 flex-col">
              <span className="text-sm font-semibold text-foreground">{option.title}</span>
              <span className="truncate text-xs font-medium text-muted-foreground">
                {option.detail}
              </span>
            </span>
          </RadioGroupPrimitive.Item>
        ))}
      </RadioGroupPrimitive.Root>
    </div>
  );
}
