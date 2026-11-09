import { ArrowUpRight, Clock, Info } from "lucide-react";

import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { EmptyState } from "@/components/empty-state";
import {
  COVERAGE_DOCS,
  freshnessLabel,
  SOURCE_HISTORY_HELP,
  SOURCE_HISTORY_UNAVAILABLE,
} from "@/lib/freshness";
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
  explanation: { computed: string; notComputed: string; live?: string };
  /** The figures include every trace so far: says "Live" instead of the time. */
  live?: boolean;
  /** `sourceHistoryLabel`: replaces the time and pending count when source history is unavailable. */
  history?: string | null;
}

/**
 * Figma "Freshness": a chip saying when the figures were last recomputed and how many traces
 * they don't include yet. The button wraps the info icon so the tooltip (opening below, clear of
 * the page title) points at the icon, and its `after:` layer stretches the hit area over the
 * whole chip: hover, focus and a touch tap anywhere on the chip open it.
 */
export function FreshnessChip({
  value,
  pendingTraces = null,
  explanation,
  history = null,
  live = false,
}: FreshnessChipProps) {
  const now = useNow(REFRESH_MS);
  if (history !== null) {
    return <Chip label={history} tooltip={SOURCE_HISTORY_HELP} />;
  }
  if (live) {
    return <Chip label="Live" tooltip={explanation.live ?? explanation.computed} />;
  }
  const computed = value !== null && !Number.isNaN(Date.parse(value));
  return (
    <Chip
      label={freshnessLabel(value, pendingTraces, now)}
      time={computed ? value : null}
      tooltip={computed ? explanation.computed : explanation.notComputed}
    />
  );
}

/** Figma "Aggregate history": the chip alone, for a page without a computed time. */
export function SourceHistoryChip({ label }: { label: string }) {
  return <Chip label={label} tooltip={SOURCE_HISTORY_HELP} />;
}

/** Figma 806:324: a list with no retained rows and no source history to recompute them from. */
export function SourceHistoryEmpty({ className }: { className?: string }) {
  return (
    <EmptyState
      icon={Clock}
      title={SOURCE_HISTORY_UNAVAILABLE}
      description={
        <>
          {SOURCE_HISTORY_HELP}{" "}
          <a
            href={COVERAGE_DOCS}
            target="_blank"
            rel="noreferrer"
            className="rounded-xs font-semibold text-accent underline underline-offset-2"
          >
            How to activate it
            <ArrowUpRight aria-hidden className="ml-0.5 inline size-3.5 align-[-2px]" />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        </>
      }
      className={className}
    />
  );
}

function Chip({
  label,
  time = null,
  tooltip,
}: {
  label: string;
  time?: string | null;
  tooltip: string;
}) {
  const { rootProps, triggerProps } = useTapTooltip();

  return (
    <span className="relative inline-flex min-w-0 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-xs font-medium text-muted-foreground has-[:focus-visible]:outline-[1.5px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring">
      <Clock aria-hidden className="size-3.5 shrink-0" strokeWidth={2} />
      <span aria-hidden className="py-1.5">
        {time === null ? label : <time dateTime={time}>{label}</time>}
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
          {tooltip}
        </TooltipContent>
      </TooltipRoot>
    </span>
  );
}
