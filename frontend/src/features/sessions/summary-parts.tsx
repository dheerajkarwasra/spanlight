import { UnknownValue } from "@/components/unknown-value";
import type { TraceSummary } from "@/lib/api";
import { formatDuration, formatInteger, formatTimestamp, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatDayTime } from "./session-format";
import { timelinePoints, type SessionTotals } from "./session-summary";

/** The Summary card's token split, turn strip and timeline rows. */

export function TokenBar({ input, output }: { input: number; output: number }) {
  const total = input + output;
  if (total === 0) {
    return <p className="text-xs font-medium text-muted-foreground">No token usage recorded.</p>;
  }

  return (
    <>
      <div aria-hidden className="flex h-2 gap-[3px]">
        {input > 0 ? (
          <span className="rounded-xs bg-chart-1" style={{ width: `${(input / total) * 100}%` }} />
        ) : null}
        {output > 0 ? (
          <span className="rounded-xs bg-chart-3" style={{ width: `${(output / total) * 100}%` }} />
        ) : null}
      </div>
      <ul className="flex flex-wrap gap-x-3.5 gap-y-1 text-xs font-medium text-muted-foreground">
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="size-2 rounded-full bg-chart-1" />
          <span className="tabular">{formatInteger(input)} input</span>
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="size-2 rounded-full bg-chart-3" />
          <span className="tabular">{formatInteger(output)} output</span>
        </li>
      </ul>
    </>
  );
}

/** Each turn as a dot along the session's time span; failed turns are larger rose dots. */
export function TurnStrip({ traces, totals }: { traces: TraceSummary[]; totals: SessionTotals }) {
  const points = timelinePoints(traces);
  const failedCount = points.filter((point) => point.failed).length;
  // Draw failures last so they sit on top of nearby successful turns.
  const ordered = [...points].sort((a, b) => Number(a.failed) - Number(b.failed));
  const span = formatDuration(totals.durationMs);
  const description = [
    `${pluralize(points.length, "turn")}${span ? ` over ${span}` : ""}`,
    failedCount > 0 ? pluralize(failedCount, "failed turn") : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div role="img" aria-label={description} className="relative h-4">
      <span className="absolute inset-x-1 top-1/2 h-0.5 -translate-y-1/2 rounded-full bg-border" />
      {ordered.map((point) => (
        <span
          key={point.traceId}
          className={cn(
            "absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full",
            point.failed
              ? "size-2 bg-chart-2 ring-2 ring-surface"
              : "size-1.5 bg-chart-1 ring-1 ring-surface",
          )}
          style={{ left: `calc(7px + ${point.offset} * (100% - 14px))` }}
        />
      ))}
    </div>
  );
}

export function TimelineRow({ label, iso }: { label: string; iso: string | null }) {
  const value = iso === null ? null : formatDayTime(iso, new Date(), { seconds: true });
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="font-mono text-label text-foreground tabular">
        {iso === null || value === null ? (
          <UnknownValue reason="No timing data" />
        ) : (
          <time dateTime={iso} title={formatTimestamp(iso) ?? undefined}>
            {value}
          </time>
        )}
      </dd>
    </div>
  );
}
