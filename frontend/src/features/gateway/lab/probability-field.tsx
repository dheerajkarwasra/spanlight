import { Slider as SliderPrimitive } from "radix-ui";
import { useId } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

import { parsePercentText } from "./scenario-params";

interface ProbabilityFieldProps {
  /** The percent as typed; the slider follows it and the input follows the slider. */
  value: string;
  onChange: (value: string) => void;
  error?: string | undefined;
}

const HINT =
  "Share of calls from attached keys that get the fault. Other calls pass through untouched. Drag in whole percents or type a value such as 0.5.";

/**
 * Figma "Probability": a slider in whole percents plus a numeric input that takes one decimal. The
 * form keeps the text; the API gets it as a 0 to 1 value with three decimals.
 */
export function ProbabilityField({ value, onChange, error }: ProbabilityFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const percent = parsePercentText(value);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <Label htmlFor={id}>Probability</Label>
        <div className="flex w-24 items-center">
          <Input
            id={id}
            inputMode="decimal"
            autoComplete="off"
            value={value}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${hintId} ${errorId}` : hintId}
            onChange={(event) => {
              onChange(event.target.value);
            }}
            className="h-10 pr-8 text-right"
          />
          <span aria-hidden className="-ml-7 text-sm text-subtle-foreground">
            %
          </span>
        </div>
      </div>
      <SliderPrimitive.Root
        min={0}
        max={100}
        step={1}
        value={[percent ?? 0]}
        onValueChange={([next]) => {
          onChange(String(next ?? 0));
        }}
        className="relative flex h-6 w-full touch-none items-center select-none"
      >
        <SliderPrimitive.Track className="relative h-1.5 grow rounded-full bg-border-strong">
          <SliderPrimitive.Range className="absolute h-full rounded-full bg-accent" />
        </SliderPrimitive.Track>
        <SliderPrimitive.Thumb
          aria-label="Probability in percent"
          aria-valuetext={`${percent ?? 0} %`}
          className={cn(
            "block size-5 rounded-full border-2 border-accent bg-surface shadow-card",
            "focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring",
          )}
        />
      </SliderPrimitive.Root>
      <div aria-hidden className="flex justify-between text-xs font-medium text-subtle-foreground">
        <span>0 %</span>
        <span>50 %</span>
        <span>100 %</span>
      </div>
      <p id={hintId} className="text-xs font-medium text-muted-foreground">
        {HINT}
      </p>
      {error ? (
        <p id={errorId} className="text-xs font-medium text-danger-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
