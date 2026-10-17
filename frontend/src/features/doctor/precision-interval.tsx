import { cn } from "@/lib/utils";

import { PRECISION_TARGET, type PrecisionRowView } from "./precision-format";

const FLOOR_LEFT = `${String(PRECISION_TARGET.lowerBound * 100)}%`;
const TARGET_LEFT = `${String(PRECISION_TARGET.precision * 100)}%`;

interface PrecisionIntervalProps {
  bar: PrecisionRowView["bar"];
  /** From 20 answers on: violet; fewer are drawn grey and never marked. */
  marked: boolean;
}

/**
 * Figma "Doctor/Precision" strip: a 0–1 axis with the lower-bound floor (0.6, dashed), the
 * target (0.8, solid), the Wilson 95 % interval as a pill and the precision as a dot. It is
 * decorative: the cell beside it carries the interval as text for screen readers.
 */
export function PrecisionInterval({ bar, marked }: PrecisionIntervalProps) {
  return (
    <div aria-hidden className="relative h-6 w-full min-w-40">
      <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border" />
      <span
        className="absolute top-1 bottom-1 border-l border-dashed border-subtle-foreground"
        style={{ left: FLOOR_LEFT }}
      />
      <span
        className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-foreground"
        style={{ left: TARGET_LEFT }}
      />
      {bar === null ? null : (
        <>
          <span
            className={cn(
              "absolute top-1/2 h-2.5 -translate-y-1/2 rounded-full",
              marked
                ? "border-2 border-accent bg-accent-subtle"
                : "border border-subtle-foreground bg-surface-muted",
            )}
            style={{ left: `${String(bar.low)}%`, width: `${String(bar.high - bar.low)}%` }}
          />
          <span
            className={cn(
              "absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full",
              marked ? "bg-accent" : "bg-subtle-foreground",
            )}
            style={{ left: `${String(bar.point)}%` }}
          />
        </>
      )}
    </div>
  );
}

/** The axis under the "Wilson 95 %" header: 0, the floor, the target and 1. */
export function PrecisionAxis() {
  return (
    <div aria-hidden className="relative h-4 w-full min-w-40 text-2xs font-medium tabular">
      <span className="absolute left-0">0</span>
      <span className="absolute -translate-x-1/2" style={{ left: FLOOR_LEFT }}>
        {PRECISION_TARGET.lowerBound}
      </span>
      <span className="absolute -translate-x-1/2" style={{ left: TARGET_LEFT }}>
        {PRECISION_TARGET.precision}
      </span>
      <span className="absolute right-0">1</span>
    </div>
  );
}
