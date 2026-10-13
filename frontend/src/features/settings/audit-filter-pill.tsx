import { useId } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import type { FilterOption } from "./audit-filter-options";

/** Figma "Settings/Filter pill": 36px, `border/strong` at rest, violet on `surface/selected` when set. */
export const PILL_BASE =
  "h-9 rounded-full border border-border-strong bg-surface text-sm shadow-none";
export const PILL_ACTIVE =
  "border-accent bg-surface-selected text-accent hover:bg-surface-selected";

const ALL = "__all__";

interface FilterSelectPillProps {
  /** "Action": the muted word before the value. */
  label: string;
  /** What no filter reads as, e.g. "All actions". */
  allLabel: string;
  /** The chosen value, or undefined for no filter. */
  value: string | undefined;
  options: FilterOption[];
  onChange: (value: string | undefined) => void;
}

/** A select drawn as a filter pill: "Action  All actions ⌄", violet once a value is chosen. */
export function FilterSelectPill({
  label,
  allLabel,
  value,
  options,
  onChange,
}: FilterSelectPillProps) {
  const active = value !== undefined;
  const labelId = useId();
  const valueId = useId();

  return (
    <Select
      value={value ?? ALL}
      onValueChange={(next) => {
        onChange(next === ALL ? undefined : next);
      }}
    >
      <SelectTrigger
        // The trigger takes no name from its content: name it "Action All actions".
        aria-labelledby={`${labelId} ${valueId}`}
        className={cn(
          PILL_BASE,
          "w-auto max-w-64 justify-start gap-1.5 py-0 pr-3 pl-3.5",
          active
            ? "border-accent bg-surface-selected [&>svg]:text-accent"
            : "[&>svg]:text-foreground",
        )}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <span id={labelId} className="shrink-0 font-medium text-muted-foreground">
            {label}
          </span>
          <span
            id={valueId}
            className={cn(
              "min-w-0 truncate font-semibold",
              active ? "text-accent" : "text-foreground",
            )}
          >
            <SelectValue />
          </span>
        </span>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{allLabel}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
