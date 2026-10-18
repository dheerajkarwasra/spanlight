import { useId } from "react";

import { Switch } from "@/components/ui/switch";

interface ToggleTileProps {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

/** Figma "Evals/Field" toggle: a muted tile with the label, a hint and a switch. */
export function ToggleTile({ label, description, checked, onCheckedChange }: ToggleTileProps) {
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-4 rounded-tile bg-surface-muted px-3.5 py-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <label htmlFor={id} className="text-sm font-semibold text-foreground">
          {label}
        </label>
        <p id={`${id}-hint`} className="text-xs font-medium text-muted-foreground">
          {description}
        </p>
      </div>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        aria-describedby={`${id}-hint`}
      />
    </div>
  );
}
