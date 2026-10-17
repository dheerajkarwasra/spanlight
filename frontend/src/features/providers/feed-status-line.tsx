import { StatusDot } from "@/components/status-dot";
import { Badge } from "@/components/ui/badge";
import type { StatusSource } from "@/lib/api";
import { formatRelativeTime, pluralize } from "@/lib/format";
import { feedLabel } from "@/lib/provider-label";

import { staleFeed } from "./error-series";

interface FeedStatusLineProps {
  sources: readonly StatusSource[];
  now: Date;
}

/** Each status feed's last good fetch, its failed polls, and "Stale" past 30 minutes or a failure. */
export function FeedStatusLine({ sources, now }: FeedStatusLineProps) {
  if (sources.length === 0) {
    return null;
  }
  return (
    <ul
      aria-label="Status feeds"
      className="flex flex-col gap-1.5 rounded-tile bg-surface-muted p-3.5 text-xs font-medium"
    >
      {sources.map((source) => {
        const stale = staleFeed(source, now);
        const fetched =
          source.last_success_at === null
            ? "never fetched"
            : `last fetched ${formatRelativeTime(source.last_success_at, now) ?? "at an unknown time"}`;
        const failures =
          source.consecutive_failures > 0
            ? `, ${pluralize(source.consecutive_failures, "failed poll")}`
            : "";
        return (
          <li key={source.provider} className="flex items-start gap-2">
            {/* The dot stays on the first line; the text and the badge wrap beside it. */}
            <StatusDot state={stale ? "warning" : "ok"} className="mt-[5px]" />
            <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
              <span className={stale ? "text-foreground" : "text-muted-foreground"}>
                {feedLabel(source.provider)} feed: {fetched}
                {failures}
              </span>
              {stale ? <Badge variant="warning">Stale</Badge> : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
