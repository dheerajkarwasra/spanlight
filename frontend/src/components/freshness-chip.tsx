import { Clock, Info } from "lucide-react";

import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { freshnessLabel } from "@/lib/freshness";
import { useNow } from "@/lib/use-now";
import { useTapTooltip } from "@/lib/use-tap-tooltip";

/** Often enough that "today" turns into "yesterday" soon after midnight. */
const REFRESH_MS = 60_000;

export interface FreshnessChipProps {
  /** `computed_at` from the API: ISO timestamp, or null when nothing is computed yet. */
  value: string | null;
  /** `pending_traces` from the API; left out where the API has none (the user detail). */
  pendingTraces?: number | null;
  /** The tooltip's text, by state: the page's refresh schedule. */
  explanation: { computed: string; notComputed: string };
}

/**
 * Figma "Freshness": a chip saying when the figures were last recomputed and how many traces
 * they don't include yet. The button wraps the info icon so the tooltip (opening below, clear of
 * the page title) points at the icon, and its `after:` layer stretches the hit area over the
 * whole chip: hover, focus and a touch tap anywhere on the chip open it.
 */
export function FreshnessChip({ value, pendingTraces = null, explanation }: FreshnessChipProps) {
  const now = useNow(REFRESH_MS);
  const { rootProps, triggerProps } = useTapTooltip();
  const label = freshnessLabel(value, pendingTraces, now);
  const computed = value !== null && !Number.isNaN(Date.parse(value));

  return (
    <span className="relative inline-flex min-w-0 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-xs font-medium text-muted-foreground has-[:focus-visible]:outline-[1.5px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring">
      <Clock aria-hidden className="size-3.5 shrink-0" strokeWidth={2} />
      <span aria-hidden className="py-1.5">
        {computed ? <time dateTime={value}>{label}</time> : label}
      </span>
      <TooltipRoot {...rootProps}>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            {...triggerProps}
            className="flex cursor-help items-center self-stretch after:absolute after:inset-0 after:rounded-full focus-visible:outline-none"
          >
            <Info aria-hidden className="size-3.5 shrink-0" strokeWidth={2} />
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" collisionPadding={16} className="max-w-52">
          {computed ? explanation.computed : explanation.notComputed}
        </TooltipContent>
      </TooltipRoot>
    </span>
  );
}
