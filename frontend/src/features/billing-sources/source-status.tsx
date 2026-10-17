import { Tooltip } from "@/components/ui/tooltip";
import type { BillingSource } from "@/lib/api";
import { cn } from "@/lib/utils";

import { statusView, type StatusTone } from "./source-model";

const TONES: Record<StatusTone, { chip: string; dot: string }> = {
  success: { chip: "bg-success-subtle text-success", dot: "bg-success" },
  danger: { chip: "bg-danger-subtle text-danger-text", dot: "bg-danger" },
  neutral: {
    chip: "border border-border bg-surface text-muted-foreground",
    dot: "bg-subtle-foreground",
  },
};

/**
 * Figma "Gateway/Check status chip" as the billing source row uses it: OK with when it was last
 * fetched, Failing with the status line, or Never fetched. The words carry the state (the dot is
 * decorative), and the tooltip, which opens on focus as on hover, explains it.
 */
export function SourceStatus({ source }: { source: BillingSource }) {
  const view = statusView(source);
  const tone = TONES[view.tone];

  return (
    <Tooltip content={view.tooltip} caret="start" side="top">
      <span
        tabIndex={0}
        role="img"
        aria-label={view.accessibleName}
        className={cn(
          "inline-flex max-w-full cursor-help items-center gap-1.5 rounded-full py-1 pr-2.5 pl-2 text-xs font-semibold",
          tone.chip,
        )}
      >
        <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", tone.dot)} />
        <span className="truncate">{view.label}</span>
      </span>
    </Tooltip>
  );
}
