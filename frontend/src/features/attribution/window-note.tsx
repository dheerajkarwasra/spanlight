import { CalendarDays } from "lucide-react";

import { FreshnessChip } from "@/components/freshness-chip";
import { Badge } from "@/components/ui/badge";
import type { AttributionResponse } from "@/lib/api";
import { describeRange, type ResolvedRange } from "@/lib/time-range";

const DAY_MS = 24 * 3_600_000;

/** The rollup refresh schedule (`REFRESH_ATTRIBUTION` in `app/jobs/scheduler.py`). */
const ROLLUP_EXPLANATION = {
  computed:
    "Longer windows read a daily rollup, recomputed every 15 minutes for yesterday and today and once a day for the week before.",
  notComputed:
    "Not computed yet. The daily rollup is recomputed every 15 minutes; the first pass for this window has not run.",
};

const UTC_DAY = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/**
 * "Sep 16 – Oct 15 · 30 days" for the rollup's UTC days (`to` is exclusive, so the last day shown
 * is the day before it); the shell's label for a live window. A rollup page adds how fresh it is.
 */
export function WindowNote({
  range,
  page,
}: {
  range: ResolvedRange;
  page: AttributionResponse | undefined;
}) {
  const source = page?.source ?? (range.durationMs <= DAY_MS ? "raw" : "rollup");
  let label = describeRange(range);
  if (page && source === "rollup") {
    const from = new Date(page.window.from);
    const to = new Date(page.window.to);
    const days = Math.round((to.getTime() - from.getTime()) / DAY_MS);
    const lastDay = new Date(to.getTime() - 1);
    label = `${UTC_DAY.format(from)} – ${UTC_DAY.format(lastDay)} · ${days} days`;
  }
  return (
    <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
      <CalendarDays aria-hidden className="size-4" />
      <span>{label}</span>
      {source === "raw" ? (
        <Badge variant="success" size="sm">
          Live
        </Badge>
      ) : page ? (
        <FreshnessChip
          value={page.computed_at}
          pendingTraces={page.pending_traces}
          explanation={ROLLUP_EXPLANATION}
        />
      ) : null}
    </span>
  );
}
