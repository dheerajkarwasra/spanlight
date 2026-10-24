import { Info } from "lucide-react";

import { UnknownValue } from "@/components/unknown-value";
import { Tooltip } from "@/components/ui/tooltip";
import type { Health, Kpis } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  APPROXIMATE_LINE,
  componentRows,
  FORMULA_LINE,
  formatPoints,
  NO_SCORE_REASON,
} from "./doctor-format";

interface DoctorScoreProps {
  health: Health;
  metrics: Kpis;
  /** False only when the detector runs are known to be none (no pass yet). */
  hasPass: boolean;
}

/**
 * The Doctor's score components in Needs attention's footer (spec §4): each component's value
 * and the points it takes off, with how the score is made in a tooltip.
 */
export function DoctorScore({ health, metrics, hasPass }: DoctorScoreProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
        Doctor score
        <Tooltip
          content={
            <p className="w-56 font-normal">
              {FORMULA_LINE}
              {health.approximate ? `. ${APPROXIMATE_LINE}` : null}
            </p>
          }
        >
          <button
            type="button"
            aria-label="How the Doctor score is calculated"
            className="relative inline-flex size-4 items-center justify-center rounded-full text-muted-foreground after:absolute after:-inset-1"
          >
            <Info aria-hidden className="size-3.5" />
          </button>
        </Tooltip>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs @lg:grid-cols-4">
        {componentRows(health, metrics, hasPass).map((row) => (
          <div key={row.name} className="flex min-w-0 flex-col">
            <dt className="font-medium text-muted-foreground">{row.label}</dt>
            <dd className="truncate text-foreground">
              {row.value ?? <UnknownValue reason={NO_SCORE_REASON} />}
            </dd>
            <dd
              className={cn(
                "tabular",
                row.penalty ? "font-semibold text-foreground" : "text-subtle-foreground",
              )}
            >
              {row.penalty === null ? "—" : formatPoints(row.penalty, row.cap)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
