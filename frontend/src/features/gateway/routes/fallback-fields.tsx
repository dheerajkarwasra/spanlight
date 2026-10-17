import { Check } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import { useId } from "react";
import { useController } from "react-hook-form";

import { SectionCard } from "@/components/section-card";
import type { FallbackCondition } from "@/lib/api";
import { cn } from "@/lib/utils";

import { FALLBACK_LABELS } from "./config-labels";
import { FALLBACK_CONDITIONS, toggledConditions, type RouteFormValues } from "./route-form";

/**
 * The Fallback card: the §9.1 conditions and the opt-in `auth_error` as checkbox cards, each with
 * its config value in mono. The value keeps the canonical order whatever order they are ticked in.
 */
export function FallbackFields() {
  const labelId = useId();
  const { field } = useController<RouteFormValues, "fallback.on">({ name: "fallback.on" });
  const selected = field.value;

  function toggle(condition: FallbackCondition, checked: boolean) {
    field.onChange(toggledConditions(selected, condition, checked));
  }

  return (
    <SectionCard
      title="Fallback"
      description="When the current target fails with one of these, the next target is tried."
      className="gap-4"
    >
      <div role="group" aria-labelledby={labelId} className="grid gap-3 sm:grid-cols-2">
        <span id={labelId} className="sr-only">
          Fallback conditions
        </span>
        {FALLBACK_CONDITIONS.map((condition) => (
          <ConditionOption
            key={condition}
            condition={condition}
            checked={selected.includes(condition)}
            onCheckedChange={(checked) => {
              toggle(condition, checked);
            }}
          />
        ))}
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        Retries and fallbacks stop once the first byte reaches your app.
      </p>
    </SectionCard>
  );
}

interface ConditionOptionProps {
  condition: FallbackCondition;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

/** Figma "Checkbox option" in its compact form: the whole card is the label. */
function ConditionOption({ condition, checked, onCheckedChange }: ConditionOptionProps) {
  return (
    <label
      className={cn(
        "flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 rounded-input transition-colors",
        // The border grows from 1 to 1.5 px when checked; the padding gives it back.
        checked
          ? "border-[1.5px] border-accent bg-surface-selected px-[13.5px] py-[11.5px]"
          : "border border-border-strong bg-surface px-3.5 py-3 hover:bg-surface-muted",
      )}
    >
      <CheckboxPrimitive.Root
        checked={checked}
        onCheckedChange={(next) => {
          onCheckedChange(next === true);
        }}
        className="flex size-[18px] shrink-0 items-center justify-center rounded-checkbox border-[1.5px] border-subtle-foreground bg-surface data-[state=checked]:border-accent data-[state=checked]:bg-accent"
      >
        <CheckboxPrimitive.Indicator>
          <Check aria-hidden className="size-3 text-accent-foreground" strokeWidth={3} />
        </CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>
      <span className="text-sm font-semibold text-foreground">{FALLBACK_LABELS[condition]}</span>
      <span className="rounded-full bg-surface-muted px-2 py-0.5 font-mono text-2xs text-muted-foreground">
        {condition}
      </span>
    </label>
  );
}
