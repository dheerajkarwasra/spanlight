import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { PrecisionKind } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { formatPrecisionRow } from "./precision-format";
import { PrecisionInterval } from "./precision-interval";
import { PrecisionStatusMark, PrecisionValue } from "./precision-table";

/** How many checks a phone shows before "Show all N checks". */
const FIRST_CARDS = 5;

function count(value: number): string {
  return formatInteger(value) ?? String(value);
}

/** Figma "Doctor/Precision — 375": one tile per check, the busiest five first. */
export function PrecisionCards({ rows }: { rows: readonly PrecisionKind[] }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? rows : rows.slice(0, FIRST_CARDS);
  return (
    <div className="flex flex-col gap-3">
      <ul aria-label="Precision per check" className="flex flex-col gap-2">
        {shown.map((row) => {
          const view = formatPrecisionRow(row);
          const marked = view.status === "meets" || view.status === "below";
          return (
            <li key={row.kind} className="flex flex-col gap-1.5 rounded-tile bg-surface-muted p-3">
              <div className="flex items-start justify-between gap-2">
                <span className="text-sm font-semibold text-foreground">{row.label}</span>
                <PrecisionStatusMark view={view} />
              </div>
              <p className="text-xs font-medium text-muted-foreground tabular">
                {count(row.right)} right · {count(row.wrong)} wrong · n {count(row.n)} · precision{" "}
                <PrecisionValue view={view} />
                <span className="sr-only">, Wilson 95&nbsp;% {view.interval ?? "unknown"}</span>
              </p>
              <PrecisionInterval bar={view.bar} marked={marked} />
            </li>
          );
        })}
      </ul>
      {rows.length > FIRST_CARDS && !showAll ? (
        <Button
          className="w-fit max-md:h-11"
          onClick={() => {
            setShowAll(true);
          }}
        >
          Show all {count(rows.length)} checks
        </Button>
      ) : null}
    </div>
  );
}
