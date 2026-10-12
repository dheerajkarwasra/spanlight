import { CircleAlert, Check } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import { useId } from "react";

import type { KeyScope } from "@/lib/api";
import { cn } from "@/lib/utils";

import { KEY_SCOPE_OPTIONS, NO_KEY_SCOPE_HINT, hasKeyScope, toggleKeyScope } from "./tokens/scopes";
import { ScopeTag } from "./tokens/scope-tag";

interface ApiKeyScopeFieldProps {
  selected: readonly KeyScope[];
  onChange: (selected: KeyScope[]) => void;
}

/**
 * Figma "Scopes": one checkbox per scope the API supports (`ingest:write`, `traces:read`,
 * `scores:write`, `prompts:read`). At least one must stay checked; with none, the dialog says so
 * and its Create button is disabled.
 */
export function ApiKeyScopeField({ selected, onChange }: ApiKeyScopeFieldProps) {
  const labelId = useId();
  const hintId = useId();
  const missing = !hasKeyScope(selected);

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      aria-describedby={missing ? hintId : undefined}
      className="flex flex-col gap-2"
    >
      <span id={labelId} className="text-label leading-5 font-semibold text-foreground">
        Scopes
      </span>
      {KEY_SCOPE_OPTIONS.map((option) => (
        <ScopeCheckbox
          key={option.value}
          option={option}
          checked={selected.includes(option.value)}
          onCheckedChange={(checked) => {
            onChange(toggleKeyScope(selected, option.value, checked));
          }}
        />
      ))}
      {missing ? (
        <p
          id={hintId}
          role="alert"
          className="flex items-start gap-1.5 text-xs font-medium text-danger-text"
        >
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
          {NO_KEY_SCOPE_HINT}
        </p>
      ) : null}
    </div>
  );
}

interface ScopeCheckboxProps {
  option: (typeof KEY_SCOPE_OPTIONS)[number];
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

/** Figma "Settings/Checkbox option": the whole card is the label, so a click anywhere toggles it. */
function ScopeCheckbox({ option, checked, onCheckedChange }: ScopeCheckboxProps) {
  const descriptionId = useId();

  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-input transition-colors",
        // The border grows from 1 to 1.5 px when checked; the padding gives it back.
        checked
          ? "border-[1.5px] border-accent bg-surface-selected p-[13.5px]"
          : "border border-border-strong bg-surface p-3.5 hover:bg-surface-muted",
      )}
    >
      <span className="flex h-[21px] w-[18px] shrink-0 items-center justify-center">
        <CheckboxPrimitive.Root
          checked={checked}
          onCheckedChange={(next) => {
            onCheckedChange(next === true);
          }}
          aria-describedby={descriptionId}
          className="flex size-[18px] items-center justify-center rounded-checkbox border-[1.5px] border-subtle-foreground bg-surface data-[state=checked]:border-accent data-[state=checked]:bg-accent"
        >
          <CheckboxPrimitive.Indicator>
            <Check aria-hidden className="size-3 text-accent-foreground" strokeWidth={3} />
          </CheckboxPrimitive.Indicator>
        </CheckboxPrimitive.Root>
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-semibold text-foreground">{option.title}</span>
          <ScopeTag scope={option.value} />
        </span>
        <span id={descriptionId} className="text-xs font-medium text-muted-foreground">
          {option.description}
        </span>
      </span>
    </label>
  );
}
