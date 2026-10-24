import { ChevronDown, ChevronUp } from "lucide-react";
import { Fragment, useId, useState, type CSSProperties } from "react";

import { Button } from "@/components/ui/button";
import { pluralize } from "@/lib/format";
import { lineDiff } from "@/lib/text-diff";
import { cn } from "@/lib/utils";

import { alignDiff, type AlignedRow } from "./output-diff-rows";

interface OutputDiffProps {
  /** Each run's answer; null when that side has none (not in the run, or the call failed). */
  a: string | null;
  b: string | null;
  names: { a: string; b: string };
}

/** Spec ruling 7: "Show diff" under the two answers opens them as one aligned line diff. */
export function OutputDiff({ a, b, names }: OutputDiffProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (a === null || b === null) {
    return null;
  }
  if (a === b) {
    return <p className="text-xs font-medium text-muted-foreground">Same output</p>;
  }
  return (
    <div className="flex flex-col gap-2.5">
      <Button
        size="sm"
        className="self-start"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronUp aria-hidden /> : <ChevronDown aria-hidden />}
        {open ? "Hide diff" : "Show diff"}
      </Button>
      {open ? <DiffBlock id={id} rows={alignDiff(lineDiff(a, b))} names={names} /> : null}
    </div>
  );
}

const CELL =
  "flex items-start gap-1.5 border-t border-border px-2.5 py-1.5 md:[grid-row:var(--row)]";

/**
 * Both sides in one grid. The DOM holds all of A, then all of B, so below `md` (one column) they
 * stack and a screen reader reads A then B; from `md` each cell is placed on its row, so the
 * same line sits side by side and the grid keeps a wrapped line's row height on both sides.
 */
function DiffBlock({
  id,
  rows,
  names,
}: {
  id: string;
  rows: AlignedRow[];
  names: OutputDiffProps["names"];
}) {
  return (
    <div
      id={id}
      role="group"
      aria-label="Output diff"
      className="grid overflow-hidden rounded-tile border border-border font-mono text-xs md:grid-cols-2"
    >
      {(["a", "b"] as const).map((side) => (
        <Fragment key={side}>
          <div
            style={{ "--row": 1 } as CSSProperties}
            className={cn(
              CELL,
              "bg-surface-muted py-2 font-sans text-overline font-semibold text-subtle-foreground uppercase md:border-t-0",
              side === "a" ? "border-t-0" : "md:col-start-2 md:border-l",
            )}
          >
            {side.toUpperCase()} · {names[side]}
          </div>
          {rows.map((row, index) => (
            <DiffCell key={index} row={row} side={side} gridRow={index + 2} />
          ))}
        </Fragment>
      ))}
    </div>
  );
}

function DiffCell({ row, side, gridRow }: { row: AlignedRow; side: "a" | "b"; gridRow: number }) {
  const style = { "--row": gridRow } as CSSProperties;
  if (row.kind === "collapsed") {
    // One full-width row from `md`; stacked, each side shows its own copy.
    return (
      <div
        style={style}
        className={cn(
          CELL,
          "justify-center bg-surface-muted font-sans font-medium text-muted-foreground",
          side === "a" ? "md:col-span-2" : "md:hidden",
        )}
      >
        {pluralize(row.count ?? 0, "unchanged line")}
      </div>
    );
  }
  const text = row[side];
  const number = side === "a" ? row.aNumber : row.bNumber;
  const changed = row.kind === "changed" && text !== null;
  return (
    <div
      style={style}
      className={cn(
        CELL,
        side === "b" && "md:col-start-2 md:border-l",
        // A padding cell exists only to hold the row open beside the other side.
        text === null && "hidden md:flex",
        changed && (side === "a" ? "bg-danger-subtle" : "bg-success-subtle"),
      )}
    >
      <span aria-hidden className="w-6 shrink-0 text-right text-subtle-foreground tabular">
        {number}
      </span>
      <span
        aria-hidden
        className={cn("w-3 shrink-0", side === "a" ? "text-danger-text" : "text-success")}
      >
        {changed ? (side === "a" ? "−" : "+") : ""}
      </span>
      <span
        className={cn(
          "min-w-0 flex-1 [overflow-wrap:anywhere] whitespace-pre-wrap",
          changed && side === "a" ? "text-danger-text" : "text-foreground",
        )}
      >
        {changed ? <span className="sr-only">{side === "a" ? "Removed: " : "Added: "}</span> : null}
        {text}
      </span>
    </div>
  );
}
