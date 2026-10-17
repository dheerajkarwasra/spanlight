import { CalendarDays } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { AttributionResponse } from "@/lib/api";
import { describeRange, type ResolvedRange } from "@/lib/time-range";

const DAY_MS = 24 * 3_600_000;

const UTC_DAY = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/**
 * "Sep 16 – Oct 15 · 30 days" for the rollup's UTC days (`to` is exclusive, so the last day shown
 * is the day before it); the shell's label for a live window.
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
      ) : (
        <Badge size="sm">Updated every 15 min</Badge>
      )}
    </span>
  );
}
