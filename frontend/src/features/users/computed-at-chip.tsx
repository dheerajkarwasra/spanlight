import { Info } from "lucide-react";

import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { UNKNOWN_DASH_CLASS } from "@/components/unknown-value";
import { useNow } from "@/lib/use-now";
import { useTapTooltip } from "@/lib/use-tap-tooltip";

import { COMPUTED_AT_EXPLANATION, computedAtLabel, NOT_COMPUTED_EXPLANATION } from "./computed-at";

/** Refresh often enough that "Updated N min ago" never reads a minute stale for long. */
const REFRESH_MS = 30_000;

interface ComputedAtChipProps {
  /** `computed_at` from the Users API: ISO timestamp, or null when nothing is computed yet. */
  value: string | null;
}

/**
 * Figma "Users / computed-at": a chip saying how fresh the per-user figures are. The button
 * wraps the info icon so the tooltip (opening below, clear of the page title) points at the
 * icon, and its `after:` layer stretches the hit area over the whole chip: hover, focus and a
 * touch tap anywhere on the chip open it. Unknown reads "Updated —" ("not computed yet").
 */
export function ComputedAtChip({ value }: ComputedAtChipProps) {
  const now = useNow(REFRESH_MS);
  const { rootProps, triggerProps } = useTapTooltip();
  const label = value === null ? null : computedAtLabel(value, now);

  return (
    <span className="relative inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-xs font-medium whitespace-nowrap text-muted-foreground has-[:focus-visible]:outline-[1.5px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring">
      <span aria-hidden className="py-1.5">
        {value !== null && label !== null ? (
          <time dateTime={value}>{label}</time>
        ) : (
          // Not an `UnknownValue`: the chip's button already carries the reason, and a second
          // focusable trigger inside it would be invalid.
          <>
            Updated <span className={UNKNOWN_DASH_CLASS}>—</span>
          </>
        )}
      </span>
      <TooltipRoot {...rootProps}>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label ?? "Updated: not computed yet"}
            {...triggerProps}
            className="flex cursor-help items-center self-stretch after:absolute after:inset-0 after:rounded-full focus-visible:outline-none"
          >
            <Info aria-hidden className="size-3.5 shrink-0" strokeWidth={2} />
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" collisionPadding={16} className="max-w-52">
          {label === null ? NOT_COMPUTED_EXPLANATION : COMPUTED_AT_EXPLANATION}
        </TooltipContent>
      </TooltipRoot>
    </span>
  );
}
