import { X } from "lucide-react";
import type { ReactNode } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import type { ActiveFilter } from "./use-trace-filters";

const ANY = "__any__";

/** Figma "Traces/Filter chip": 36px pills. */
export const CHIP_BASE =
  "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full px-3.5 text-sm font-medium";

interface FacetSelectProps {
  label: string;
  value: string | undefined;
  options: readonly string[];
  loading?: boolean;
  optionLabel?: (value: string) => string;
  onChange: (value: string | undefined) => void;
}

/** Figma filter chip, default state: "Status: Any ⌄". */
export function FacetSelect({
  label,
  value,
  options,
  loading = false,
  optionLabel = (option) => option,
  onChange,
}: FacetSelectProps) {
  // Keep a value from the URL selectable even if it's no longer in the 30-day options list.
  const choices = value !== undefined && !options.includes(value) ? [value, ...options] : options;
  const isEmpty = !loading && choices.length === 0;

  return (
    <Select
      value={value ?? ANY}
      onValueChange={(next) => {
        onChange(next === ANY ? undefined : next);
      }}
      disabled={isEmpty}
    >
      <SelectTrigger
        aria-label={label}
        title={isEmpty ? `No ${label.toLowerCase()} values in the last 30 days` : undefined}
        className={cn(
          CHIP_BASE,
          "w-auto max-w-64 justify-start border-border-strong bg-surface text-foreground [&>svg]:size-3.5 [&>svg]:text-foreground",
          value !== undefined && "border-accent/60",
        )}
      >
        <span className="flex min-w-0 items-center gap-1">
          <span className="shrink-0">{label}:</span>
          <span className="min-w-0 truncate">
            <SelectValue />
          </span>
        </span>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>Any</SelectItem>
        {choices.map((option) => (
          <SelectItem key={option} value={option}>
            {optionLabel(option)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function Mono({ value }: { value: string }) {
  return (
    <span title={value} className="min-w-0 truncate font-mono">
      {value}
    </span>
  );
}

/**
 * The chip's words: "environment: production", "customer_id = acme", "No customer_id" (Figma
 * "Traces / Metadata chips and filter") or "Trace name = summarize".
 */
function chipText(filter: ActiveFilter): ReactNode {
  if (filter.pair) {
    return filter.pair.value === undefined ? (
      <>
        <span className="shrink-0">No</span>
        <Mono value={filter.pair.key} />
      </>
    ) : (
      <>
        <span className="max-w-40 shrink-0 truncate font-mono">{filter.pair.key}</span>
        <span className="shrink-0">=</span>
        <Mono value={filter.pair.value} />
      </>
    );
  }
  if (filter.key === "name") {
    return (
      <>
        <span className="shrink-0">{filter.label} =</span>
        <Mono value={filter.value} />
      </>
    );
  }
  const isIdentifier = filter.key === "user" || filter.key === "session";
  return (
    <>
      <span className="shrink-0">{filter.label.toLowerCase()}:</span>
      <span title={filter.value} className={cn("min-w-0 truncate", isIdentifier && "font-mono")}>
        {filter.value}
      </span>
    </>
  );
}

/** Figma filter chip, applied state: violet "environment: production ×". */
export function AppliedFilterChip({
  filter,
  onRemove,
}: {
  filter: ActiveFilter;
  onRemove: () => void;
}) {
  return (
    <span className={cn(CHIP_BASE, "max-w-full bg-accent-subtle pr-2 text-accent")}>
      <span
        title={filter.pair ? filter.value : undefined}
        className="flex min-w-0 items-center gap-1"
      >
        {chipText(filter)}
      </span>
      {/* An 8 px vertical hit inset on phones (ruling T20-4); the chip stays 36 px. */}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${filter.pair ? filter.value : filter.label.toLowerCase()} filter`}
        className="relative -my-1 inline-flex size-6 shrink-0 items-center justify-center rounded-full transition-colors before:absolute before:inset-x-0 before:-inset-y-2 hover:bg-accent/10 md:before:hidden"
      >
        <X aria-hidden className="size-3.5" />
      </button>
    </span>
  );
}
