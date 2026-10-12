import { Check, Minus, X, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type SegmentTone = "success" | "warning" | "danger" | "neutral";

export interface Segment {
  label: string;
  count: number;
  tone: SegmentTone;
}

const FILL: Record<SegmentTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  neutral: "bg-border-strong",
};

const TEXT: Record<SegmentTone, string> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger-text",
  neutral: "text-muted-foreground",
};

const ICON: Record<SegmentTone, LucideIcon> = {
  success: Check,
  warning: Minus,
  danger: X,
  neutral: Minus,
};

/**
 * A 10 px bar split by count (Figma "Distribution"). Decorative: the counts are always written
 * next to it, as pills or as the legend.
 */
export function DistributionBar({ segments }: { segments: readonly Segment[] }) {
  const shown = segments.filter((segment) => segment.count > 0);
  if (shown.length === 0) {
    return null;
  }
  return (
    <div aria-hidden className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full">
      {shown.map((segment) => (
        <span
          key={segment.label}
          className={cn("h-full min-w-px", FILL[segment.tone])}
          style={{ flexGrow: segment.count, flexBasis: 0 }}
        />
      ))}
    </div>
  );
}

/** "✓ good 28  – ok 13  ✕ bad 7": each category with its icon and count. */
export function DistributionLegend({ segments }: { segments: readonly Segment[] }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1">
      {segments.map((segment) => {
        const Icon = ICON[segment.tone];
        return (
          <li
            key={segment.label}
            className={cn("flex items-center gap-1 text-xs font-semibold", TEXT[segment.tone])}
          >
            <Icon aria-hidden className="size-3" strokeWidth={3} />
            {segment.label} {segment.count}
          </li>
        );
      })}
    </ul>
  );
}
