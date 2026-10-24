import { Check, Minus } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";

interface RowCheckboxProps {
  checked: boolean | "indeterminate";
  onCheckedChange: () => void;
  label: string;
}

/**
 * Figma "Traces — Bulk selection" checkbox: ink when ticked, a dash when part of the list is.
 * The 18 px box has a 24 px hit area (WCAG 2.5.8), so it fits 32 px compact rows.
 */
export function RowCheckbox({ checked, onCheckedChange, label }: RowCheckboxProps) {
  return (
    <CheckboxPrimitive.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      aria-label={label}
      className="relative flex size-[18px] items-center justify-center rounded-checkbox border-[1.5px] border-subtle-foreground bg-surface after:absolute after:-inset-[3px] data-[state=checked]:border-ink data-[state=checked]:bg-ink data-[state=indeterminate]:border-ink data-[state=indeterminate]:bg-ink"
    >
      <CheckboxPrimitive.Indicator>
        {checked === "indeterminate" ? (
          <Minus aria-hidden className="size-3 text-ink-foreground" strokeWidth={3} />
        ) : (
          <Check aria-hidden className="size-3 text-ink-foreground" strokeWidth={3} />
        )}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
