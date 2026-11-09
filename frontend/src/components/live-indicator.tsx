import { LoaderCircle } from "lucide-react";

import { StatusDot } from "@/components/status-dot";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import type { LiveState, LiveStatus } from "@/lib/live/live-state";
import { useTapTooltip } from "@/lib/use-tap-tooltip";

const COPY: Record<LiveStatus, { label: string; tooltip: string; announce: string }> = {
  live: {
    label: "Live",
    tooltip: "Updates as new traces arrive.",
    announce: "Live updates on",
  },
  reconnecting: {
    label: "Reconnecting",
    tooltip: "Connection lost. Reconnecting now; figures can be a few seconds behind.",
    announce: "Reconnecting",
  },
  paused: {
    label: "Paused",
    tooltip: "Paused while this tab is hidden. Updates resume when you come back.",
    announce: "Live updates paused",
  },
  polling: {
    label: "Updating every 15 s",
    tooltip: "Live updates aren't available right now. Checking for new data every 15 seconds.",
    announce: "Updating every 15 seconds",
  },
};

interface LiveIndicatorProps {
  status: LiveState;
  /** A second tooltip paragraph, e.g. the Overview's approximate-percentiles note. */
  note?: string;
}

/**
 * Figma "Live/Indicator" (906:34): whether the page updates by itself. A focusable pill whose
 * tooltip opens on hover, focus and tap; a hidden status region announces only state changes.
 * Nothing shows while the first connection is being made, but the empty region stays mounted:
 * screen readers announce changes to a live region, not one that appears with its text.
 */
export function LiveIndicator({ status, note }: LiveIndicatorProps) {
  const { rootProps, triggerProps } = useTapTooltip();
  const copy = status === "connecting" ? null : COPY[status];

  return (
    <>
      {copy && (
        <TooltipRoot {...rootProps}>
          <TooltipTrigger asChild>
            <button
              type="button"
              {...triggerProps}
              className="relative inline-flex h-6 shrink-0 cursor-help items-center gap-1.5 rounded-full border border-border bg-surface pr-[9px] pl-2 text-2xs font-semibold tracking-[0.02em] whitespace-nowrap text-muted-foreground normal-case after:absolute after:inset-x-0 after:-inset-y-0.5 hover:text-foreground"
            >
              {status === "reconnecting" ? (
                <LoaderCircle aria-hidden className="size-3 animate-spin text-warning" />
              ) : (
                <StatusDot
                  state={status === "live" ? "live" : "unset"}
                  className={
                    status === "live" ? "ring-[1.5px] ring-foreground ring-inset" : undefined
                  }
                />
              )}
              {copy.label}
            </button>
          </TooltipTrigger>
          <TooltipContent
            side="bottom"
            caret="start"
            collisionPadding={16}
            className={note ? "w-72" : "max-w-60"}
          >
            {copy.tooltip}
            {note ? <span className="mt-2 block">{note}</span> : null}
          </TooltipContent>
        </TooltipRoot>
      )}
      <span role="status" className="sr-only">
        {copy?.announce}
      </span>
    </>
  );
}
