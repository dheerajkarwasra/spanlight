import { Info } from "lucide-react";

import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { useTapTooltip } from "@/lib/use-tap-tooltip";

/** The tooltip copy, verbatim from the hybrid metrics reads spec (§7). */
export const APPROXIMATE_COPY =
  "Percentiles are estimated from hourly latency histograms, accurate to about ±25 %. Counts and cost are exact; a span that arrives late can take up to an hour to appear.";

/**
 * "Estimated · hourly rollups" (Figma "Overview/Estimated chip"): the one explanation of the
 * "≈" prefix on percentile values, shown in the hero's meta line when the overview response is
 * `approximate` (the window read at least one rollup hour, so also the default 24 h view). A focusable button; its tooltip opens on hover and focus and closes on Esc.
 */
export function EstimatedChip() {
  const { rootProps, triggerProps } = useTapTooltip();

  return (
    <TooltipRoot {...rootProps}>
      <TooltipTrigger asChild>
        <button
          type="button"
          {...triggerProps}
          className="relative inline-flex shrink-0 cursor-help items-center gap-[5px] rounded-full border border-border bg-surface py-[3px] pr-[9px] pl-[7px] text-2xs font-semibold tracking-[0.02em] whitespace-nowrap text-muted-foreground normal-case after:absolute after:inset-x-0 after:-inset-y-0.5 hover:text-foreground"
        >
          <Info aria-hidden className="size-[13px]" />
          Estimated · hourly rollups
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" caret="start" collisionPadding={16} className="w-72">
        {APPROXIMATE_COPY}
      </TooltipContent>
    </TooltipRoot>
  );
}
